"""completar.py — FECHA O CICLO: completa a guia incompleta em vez de so avisar.

Ate 02/09 o robo detectava a guia incompleta, avisava e parava. Toda vez alguem
tinha de fazer a mao o que este modulo faz: achar o documento que falta no PRORADIS,
baixar e anexar.

O que travava era duplicacao. A idempotencia do `upload_arquivos` e por NOME de
arquivo, e quem anexa a mao usa nome livre — a guia da JESSICA (196708276) tem
"Laudo Cefalometrico.pdf", mesmo exame do "LAUDO_TELERRADIOGRAFIA_<acc>_CEPH.pdf"
do robo. Por nome, os dois subiriam. `conferencia.arquivos_que_faltam` resolve isso
comparando por TIPO DE EXAME.

Tres travas, porque anexo e irreversivel:
  1. so guia DENTRO do prazo de 7 dias (fora dele nao adianta, so recurso);
  2. so arquivo cujo exame esta comprovadamente faltando na guia;
  3. `max_antes` vem da contagem VIVA da API, nao de estimativa.
"""
import os
from datetime import date

import db
from conferencia import arquivos_que_faltam
from esteira import _anexos_via_api
from extrator_odontoprev import abrir_gto, upload_arquivos
from solicitacao_utils import canon_exames

PRAZO_DIAS = 7


def no_prazo(dia: str, hoje=None) -> bool:
    d = db._parse_ddmmaaaa(dia)
    if not d:
        return False
    return ((hoje or date.today()) - d).days <= PRAZO_DIAS


def planejar(guia, disponiveis, bearer):
    """(arquivos_a_subir, anexos_atuais, motivo_se_nao) — sem tocar no portal alem
    de LER a lista de anexos."""
    if not no_prazo(guia["dia"]):
        return [], [], "fora do prazo de 7 dias"
    n, nomes, err = _anexos_via_api(bearer, guia["gto"])
    if n < 0:
        return [], [], f"nao consegui ler os anexos ({err})"
    faltam = arquivos_que_faltam(canon_exames(guia.get("eg", "")),
                                 sorted(nomes), disponiveis)
    if not faltam:
        return [], sorted(nomes), "nada a acrescentar"
    return faltam, sorted(nomes), ""


def completar(pg, guia, arquivos, n_atual, bearer, log=None):
    """Anexa os arquivos escolhidos. Devolve o dict do upload_arquivos."""
    _log = log or (lambda m: None)
    gp = abrir_gto(pg, guia["gto"])
    try:
        r = upload_arquivos(
            gp, [os.path.abspath(a) for a in arquivos],
            max_antes=max(1, n_atual),
            contar_fallback=lambda g=guia["gto"]: _anexos_via_api(bearer, g)[:2])
        _log(f"[completar] {guia['gto']} {guia.get('paciente','')}: "
             f"ok={r.get('ok')} enviados={r.get('enviados')}")
        return r
    finally:
        try:
            gp.close()
        except Exception:
            pass


# ── orquestracao: fecha o ciclo ao fim da rodada ────────────────────────────

def _baixar_do_proradis(guias, dest, log):
    """Baixa do PRORADIS o que existe para cada guia (laudos + folhas de entrega).
    Devolve {gto: [caminhos]}. So leitura; falha de uma guia nao derruba as outras."""
    import requests
    from collections import defaultdict
    from playwright.sync_api import sync_playwright
    from extrator_arquivos import (_login_playwright, listar_worklist_por_pacientes,
                                   extrair_tokens, baixar_imagens, BASE)
    from extrator_pacientes_analitico import get_credentials
    out = defaultdict(list)
    por_dia = defaultdict(list)
    for g in guias:
        por_dia[g["dia"]].append(g)
    email, password = get_credentials()
    with sync_playwright() as pw:
        br, ctx, pg = _login_playwright(pw, email, password)
        ctx.set_default_timeout(60000)
        try:
            cj = {c["name"]: c["value"] for c in ctx.cookies()}
            sess = requests.Session()
            sess.cookies.update(cj)
            sess.headers.update({"User-Agent": "Mozilla/5.0",
                                 "Referer": f"{BASE}/reports_list"})
            for dia, lista in por_dia.items():
                nomes = [g["paciente"] for g in lista]
                try:
                    achados = listar_worklist_por_pacientes(pg, dia, nomes)
                except Exception as e:
                    log(f"[completar] worklist {dia}: {str(e)[:80]}")
                    continue
                for it in achados:
                    nm = str(it.get("nome", "")).upper().strip()
                    g = next((x for x in lista
                              if x["paciente"].upper().strip() in nm
                              or nm in x["paciente"].upper().strip()), None)
                    if not g:
                        continue
                    pasta = os.path.join(dest, str(g["gto"]))
                    os.makedirs(pasta, exist_ok=True)
                    seen, n_img = set(), 0
                    for rh in it.get("rows_html", []):
                        tk = extrair_tokens(rh)
                        for tok in (tk.get("pan") or []):
                            try:
                                r = sess.get(f"{BASE}/report/pdf?studies={tok}", timeout=60)
                            except Exception:
                                continue
                            if r.content[:4] != b"%PDF" or len(r.content) < 10000:
                                continue
                            ex = (tk.get("exame") or "EXAME").replace("/", "-")
                            cam = os.path.join(
                                pasta, f"LAUDO_{ex}_{it.get('accession')}_OFICIAL.pdf")
                            if cam in out[g["gto"]]:
                                continue
                            open(cam, "wb").write(r.content)
                            out[g["gto"]].append(cam)
                        doc = tk.get("doc")
                        if doc:
                            try:
                                r = baixar_imagens(pg, ctx, doc["study_id"],
                                                   doc["schedule_id"], pasta, seen, n_img)
                                n_img = r.get("next_n", n_img)
                                out[g["gto"]] += [os.path.join(pasta, a)
                                                  for a in (r.get("arquivos") or [])]
                            except Exception:
                                pass
        finally:
            try:
                br.close()
            except Exception:
                pass
    return out


def rodada(incompletas, log=None):
    """Completa as guias incompletas que ainda estao no prazo. (completadas, erros).

    Roda DEPOIS do faturamento e da conferencia: e recuperacao, nao faturamento."""
    from collections import defaultdict
    import tempfile
    from playwright.sync_api import sync_playwright
    from config import PLANOS
    from extrator_odontoprev import (login_odonto, abrir_consultar_gtos,
                                     consultar_periodo)
    _log = log or (lambda m: None)
    alvos = [g for g in (incompletas or []) if no_prazo(g.get("dia", ""))]
    fora = len(incompletas or []) - len(alvos)
    if fora:
        _log(f"[completar] {fora} guia(s) fora do prazo de 7 dias — so recurso")
    if not alvos:
        return [], []
    dest = tempfile.mkdtemp(prefix="completar_")
    disp = _baixar_do_proradis(alvos, dest, _log)
    feitas, erros = [], []
    por_conta = defaultdict(list)
    for g in alvos:
        por_conta[g["conta"]].append(g)
    with sync_playwright() as pw:
        for conta, lista in sorted(por_conta.items()):
            unid = PLANOS.get(conta, {}).get("label", conta)
            bearer = {"v": None}
            try:
                br, ctx, pg = login_odonto(pw, conta, db.get_portal_senha(conta))
            except Exception as e:
                _log(f"[completar] {unid}: login falhou — {str(e)[:80]}")
                erros += [(g, "login") for g in lista]
                continue
            try:
                ctx.on("request", lambda r: bearer.__setitem__(
                    "v", r.headers.get("authorization"))
                    if "credenciado.odontoprev.com.br" in r.url
                    and (r.headers.get("authorization") or "").lower().startswith("bearer")
                    else None)
                dias = sorted({g["dia"] for g in lista})
                for dia in dias:
                    try:
                        abrir_consultar_gtos(pg)
                        consultar_periodo(pg, dia)
                        pg.wait_for_timeout(2500)
                    except Exception as e:
                        _log(f"[completar] {unid} {dia}: consulta falhou — {str(e)[:70]}")
                        continue
                    for g in [x for x in lista if x["dia"] == dia]:
                        arqs, atuais, motivo = planejar(g, disp.get(g["gto"], []),
                                                        bearer["v"])
                        if motivo or not arqs:
                            _log(f"[completar] {g['gto']}: {motivo or 'nada a subir'}")
                            continue
                        try:
                            r = completar(pg, g, arqs, len(atuais), bearer["v"], log=_log)
                            (feitas if r.get("ok") else erros).append(
                                g if r.get("ok") else (g, r.get("erro") or "upload"))
                        except Exception as e:
                            _log(f"[completar] {g['gto']} ERRO: {str(e)[:90]}")
                            erros.append((g, str(e)[:90]))
            finally:
                try:
                    br.close()
                except Exception:
                    pass
    return feitas, erros
