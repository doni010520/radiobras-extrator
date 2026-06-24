"""
esteira.py — Pipeline PARALELO de 3 estágios (descoberta -> download -> leitura).
NÃO anexa. Cada estágio tem fila + pool próprios, então rodam sobrepostos.

  DESCOBERTA  (N sessões OdontoPrev): abre cada GTO alvo, conta anexos, pendente
              -> fila_pend.
  DOWNLOAD    (M sessões PRORADIS, sessão compartilhada): baixa laudo+imagens
              (rápido, ~13s) e ENTREGA pra fila_leit (não fica preso na leitura).
  LEITURA     (K sessões PRORADIS + Gemini 2.5 Flash): baixa anexos do prontuário
              e lê as solicitações via Gemini (I/O-bound; substitui o Tesseract).

A separação da leitura num pool próprio é o ponto: o download não trava na
leitura, e a leitura escala sozinha (limitada pela cota do Gemini, não pela CPU).

rodar_esteira(data, m_download, n_desc, k_leitura, log, gemini_key) -> resumo.
"""
import os
import queue
import tempfile
import threading
import time

import requests
from playwright.sync_api import sync_playwright

from config import CONVENIOS, SEGMENTOS, PLANOS
from extrator_pacientes_analitico import BASE_URL as BASE, get_credentials
from extrator_arquivos import (
    _login_playwright, _get_relatorio_analitico,
    listar_worklist_por_pacientes, _processar_paciente,
)
from extrator_odontoprev import (
    login_odonto, get_credentials_odonto, abrir_consultar_gtos,
    consultar_periodo, listar_gtos, abrir_gto, _anexos_nomes, _anexos_count,
    normaliza_nome, upload_arquivos,
)
from fechar_dia import _prefixo_casa, _ja_anexado_por_nos
from extrair_anexos_dia import anexos_do_paciente
from gto_utils import is_gto_pdf, extrair_observacao
from solicitacao_utils import gto_exames
import json
import re

try:
    import psutil
    _PROC = psutil.Process(os.getpid())
except Exception:
    psutil = None
    _PROC = None

_GEM_PROMPT = ("É uma solicitação/requisição de exames odontológicos? Se sim, responda em "
               "JSON {solicitacao:true, tipo:'digitada'|'manuscrita', legivel:bool, exames:[...]}. "
               "Se não, {solicitacao:false}. Responda só o JSON.")
_MAX_LEITURAS = 5  # teto de anexos lidos por paciente (mesmo no tier pago)


def _mem_mb():
    if not _PROC:
        return -1
    try:
        tot = _PROC.memory_info().rss
        for ch in _PROC.children(recursive=True):
            try:
                tot += ch.memory_info().rss
            except Exception:
                pass
        return tot / 1e6
    except Exception:
        return -1


def _build_by_norm(df):
    cod_col = "Cód. Pac" if "Cód. Pac" in df.columns else df.columns[1]
    ped_col = "Pedido" if "Pedido" in df.columns else df.columns[6]
    nome_col = "Paciente" if "Paciente" in df.columns else df.columns[2]
    by = {}
    for _, r in df.iterrows():
        nm = str(r[nome_col]).strip()
        lst = by.setdefault(normaliza_nome(nm), [])
        pac = next((p for p in lst if p["cod_pac"] == str(r[cod_col]).strip()), None)
        if not pac:
            pac = {"cod_pac": str(r[cod_col]).strip(), "nome": nm, "accessions": []}
            lst.append(pac)
        a = str(r[ped_col]).strip()
        if a and a not in pac["accessions"]:
            pac["accessions"].append(a)
        if len(nm) > len(pac["nome"]):
            pac["nome"] = nm
    return by


def _baixa_um(pg, ctx, by_norm, g, tmp, data):
    """ESTÁGIO 2 (download only): match + baixa laudo+imagens. Devolve item com
    _pac embutido (p/ o estágio de leitura). NÃO lê solicitação aqui."""
    t0 = time.monotonic()
    nn = g["nome_norm"]
    cands = by_norm.get(nn, [])
    if not cands:
        vistos, pref = set(), []
        for key, lst in by_norm.items():
            if _prefixo_casa(key, nn):
                for p in lst:
                    if p["cod_pac"] not in vistos:
                        vistos.add(p["cod_pac"]); pref.append(p)
        cands = pref
    if len(cands) > 1:
        return {"gto": g["gto"], "nome": g["nome"], "status": "AMBIGUO", "dt_dl": time.monotonic() - t0}
    if cands:
        pac = cands[0]
        wl = listar_worklist_por_pacientes(pg, data, [pac["nome"]])
    else:
        wl = listar_worklist_por_pacientes(pg, data, [g["nome"]])
        accs = sorted({w["accession"] for w in wl if w.get("accession")})
        toks = g["nome"].split()
        while not accs and len(toks) > 2:
            toks = toks[:-1]
            wl = listar_worklist_por_pacientes(pg, data, [" ".join(toks)])
            accs = sorted({w["accession"] for w in wl if w.get("accession")})
        if not accs:
            return {"gto": g["gto"], "nome": g["nome"], "status": "SEM_MATCH", "dt_dl": time.monotonic() - t0}
        pac = {"nome": g["nome"], "cod_pac": "WL" + accs[0], "accessions": accs}
    res = _processar_paciente(pg, ctx, pac, wl, tmp, data)
    pasta = os.path.join(tmp, res["pasta"])
    nf = len(os.listdir(pasta)) if os.path.isdir(pasta) else 0
    return {"gto": g["gto"], "nome": pac["nome"], "status": "BAIXADO",
            "arquivos": nf, "imgs": res.get("imagens", {}).get("qtd", 0),
            "_pac": pac, "_pasta": pasta, "dt_dl": time.monotonic() - t0}


_DECISAO_PROMPT = """Acima estão VÁRIOS anexos do prontuário, indexados ([anexo 0], [anexo 1], ...).
CONTEXTO DA GTO -> paciente: {paciente} | exames esperados: {exames} | DATA DO EXAME: {data_exame}

Você é auditor de solicitações odontológicas. Identifique QUAL anexo é a SOLICITAÇÃO/
REQUISIÇÃO de exames que corresponde a ESTA GTO: mesmo paciente, exames compatíveis E
DATA compatível com o exame. Ignore laudos, raios-x e solicitações ANTIGAS de outros
atendimentos que ficam guardadas no prontuário.

SOBRE A DATA (importante): leia a data escrita na solicitação. Ela deve ser PRÓXIMA à data
do exame ({data_exame}) e NÃO posterior a ela. Uma solicitação de meses ou anos antes
(ex.: ano diferente) é de OUTRO atendimento e NÃO serve para esta GTO -> anexar=false.

Responda APENAS JSON (sem markdown):
{{"indice_solicitacao": <int do anexo certo, ou null>, "tipo": "digitada"|"manuscrita"|null,
"legivel": <bool>, "paciente_lido": "<str ou null>", "exames_lidos": [<str>],
"exames_batem": <bool>, "data_solicitacao": "<DD/MM/AAAA lida na solicitação, ou null se ilegível>",
"data_bate": <bool>, "confianca": "alta"|"media"|"baixa", "anexar": <bool>, "motivo": "<curto>"}}

Regra: anexar=true SÓ se é a solicitação certa desta GTO, legível, exames batem, DATA
compatível e confiança alta. Em qualquer dúvida -> anexar=false (vai pra revisão humana)."""


def _parse_br_date(s):
    """'DD/MM/AAAA' (ou DD/MM/AA) -> date; None se não der."""
    try:
        import datetime as _dt
        p = re.findall(r"\d+", str(s))
        if len(p) < 3:
            return None
        d, m, y = int(p[0]), int(p[1]), int(p[2])
        if y < 100:
            y += 2000
        return _dt.date(y, m, d)
    except Exception:
        return None


def _decidir(gem, pg, ctx, pac, pasta_dl, review_dir=None, gto=None, data_exame=None):
    """ESTÁGIO 3 (decisão): baixa anexos do prontuário, extrai os exames da GTO e
    manda TUDO pro Gemini escolher a solicitação certa + decidir. NÃO anexa.
    Devolve plano (laudo+imgs sempre; solicitação se a IA confiar) + a decisão.
    Se review_dir/gto, salva os candidatos p/ a página de revisão."""
    from google.genai import types
    out = {"anexos": 0, "gto_exames": [], "decisao": None, "erro": None,
           "plano_laudo_imgs": [], "plano_solicitacao": None,
           "candidatos": [], "solic_idx": None, "justificativa": None}
    if pasta_dl and os.path.isdir(pasta_dl):
        out["plano_laudo_imgs"] = sorted(os.listdir(pasta_dl))
    try:
        lista = anexos_do_paciente(pg, pac["nome"], pac["cod_pac"])
    except Exception as e:
        out["erro"] = f"anexos: {str(e)[:80]}"; return out
    out["anexos"] = len(lista)
    cj = {ck["name"]: ck["value"] for ck in ctx.cookies()}
    sess = requests.Session(); sess.cookies.update(cj)
    sess.headers.update({"User-Agent": "Mozilla/5.0", "Referer": f"{BASE}/patients"})
    att_dir = tempfile.mkdtemp(prefix="_att_")
    # ordena do MAIS NOVO pro mais antigo (id desc): garante que a solicitação
    # recente entre mesmo em prontuário grande/com histórico de anos.
    def _id_key(it):
        try:
            return int(re.sub(r"\D", "", str(it.get("id", ""))) or 0)
        except Exception:
            return 0
    lista = sorted(lista, key=_id_key, reverse=True)
    cands_raw, gto_ex, justif_ok = [], set(), False
    for it in lista[:30]:
        ext = it["filename"].lower().rsplit(".", 1)[-1] if "." in it["filename"] else ""
        try:
            blob = sess.get(it["url"], timeout=60).content
        except Exception:
            continue
        path = os.path.join(att_dir, re.sub(r"[^A-Za-z0-9._-]+", "_", it["filename"]) or it["id"])
        with open(path, "wb") as f:
            f.write(blob)
        if ext == "pdf" and is_gto_pdf(path):     # pdf da GTO -> exames + justificativa
            try:
                gto_ex |= gto_exames(path)
            except Exception:
                pass
            try:
                if extrair_observacao(path).get("status") == "PREENCHIDO":
                    justif_ok = True
            except Exception:
                pass
            continue
        mime = {"pdf": "application/pdf", "png": "image/png",
                "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext)
        if mime:
            cands_raw.append((it["filename"], mime, blob))
    out["gto_exames"] = sorted(gto_ex)

    # REGRA: GTO com justificativa (campo 49) -> solicitação DISPENSADA. Nem toca
    # nos anexos do prontuário (não salva, não manda pro Gemini). Só laudo+imgs.
    if justif_ok:
        out["justificativa"] = "PREENCHIDA"
        out["decisao"] = {"anexar": False, "justificativa": True,
                          "motivo": "GTO tem justificativa (campo 49) — solicitação dispensada"}
        return out

    # sem justificativa -> precisa da solicitação: agora sim salva candidatos + Gemini
    # (os 15 mais novos — já ordenados do mais recente pro mais antigo)
    cands = []
    for fn, mime, blob in cands_raw[:15]:
        saved = None
        if review_dir and gto is not None:
            gdir = os.path.join(review_dir, str(gto))
            os.makedirs(gdir, exist_ok=True)
            saved = f"{len(cands)}__{re.sub(r'[^A-Za-z0-9._-]+', '_', fn) or 'anexo'}"
            try:
                with open(os.path.join(gdir, saved), "wb") as f:
                    f.write(blob)
            except Exception:
                saved = None
        cands.append((fn, mime, blob, saved))
    out["candidatos"] = [{"idx": i, "nome": c[0], "arquivo": c[3]} for i, c in enumerate(cands)]
    if not cands:
        out["decisao"] = {"anexar": False, "motivo": "sem anexo candidato a solicitação"}
        return out
    contents = []
    for i, (fn, mime, blob, saved) in enumerate(cands):
        contents.append(f"[anexo {i}] {fn}")
        contents.append(types.Part.from_bytes(data=blob, mime_type=mime))
    contents.append(_DECISAO_PROMPT.format(
        paciente=pac["nome"], exames=(sorted(gto_ex) or "(GTO ilegível)"),
        data_exame=(data_exame or "(desconhecida)")))
    for tent in range(3):
        try:
            r = gem.models.generate_content(model="gemini-2.5-flash", contents=contents)
            txt = re.sub(r"^```json|^```|```$", "", (r.text or "").strip(), flags=re.M).strip()
            dec = json.loads(txt)
            # TRAVA DE DATA: se a solicitação escolhida é >90 dias antes do exame
            # (ou posterior a ele), não auto-anexa -> revisão humana.
            if dec.get("anexar"):
                dsol, dexm = _parse_br_date(dec.get("data_solicitacao")), _parse_br_date(data_exame)
                if dsol and dexm:
                    gap = (dexm - dsol).days
                    if gap > 90 or gap < -1:
                        quando = "posterior ao exame" if gap < -1 else f"{gap} dias antes do exame"
                        dec["anexar"] = False
                        dec["data_flag"] = True
                        dec["motivo"] = (f"solicitação de {dec.get('data_solicitacao')} "
                                         f"({quando}) — não bate com o exame {data_exame}, revisar")
            out["decisao"] = dec
            idx = dec.get("indice_solicitacao")
            if dec.get("anexar") and isinstance(idx, int) and 0 <= idx < len(cands):
                out["plano_solicitacao"] = cands[idx][0]
                out["solic_idx"] = idx
                # salva a solicitação escolhida na pasta do laudo+imgs (4º estágio anexa tudo dali)
                if pasta_dl and os.path.isdir(pasta_dl):
                    sname = "SOLICITACAO_" + (re.sub(r"[^A-Za-z0-9._-]+", "_", cands[idx][0]) or "solic")
                    try:
                        with open(os.path.join(pasta_dl, sname), "wb") as f:
                            f.write(cands[idx][2])
                    except Exception:
                        pass
            break
        except Exception as e:
            out["erro"] = f"gemini: {str(e)[:80]}"
            time.sleep(1.0 * (tent + 1))
    return out


def rodar_esteira(data, m_download=6, n_desc=3, k_leitura=5, log=None, gemini_key=None,
                  review_dir=None, k_attach=0, dry_run=True, conta=None):
    """Pipeline de até 4 estágios (descoberta -> download -> decisão -> anexação).
    conta = código da conta RedeUna (plano); usa o login + convênios/segmentos dela.
    gemini_key liga a decisão. k_attach>0 liga a ANEXAÇÃO (estágio 4): auto e
    justificativa são anexados; sem-solicitação e revisão NÃO (ficam avisados).
    dry_run=True só simula a anexação (loga o plano, não sobe nada)."""
    if log is None:
        log = lambda m: print(m, flush=True)
    plano = PLANOS.get(conta or "")
    _convenios = plano["convenios"] if plano else CONVENIOS
    _segmentos = plano["segmentos"] if plano else SEGMENTOS
    _odo_user = conta if (conta and plano) else None   # None -> usa ODONTOPREV_USER padrão
    t_glob = time.monotonic()

    def _t(m):
        log(f"[{time.monotonic() - t_glob:6.0f}s] {m}")

    gem = None
    if gemini_key:
        try:
            from google import genai
            gem = genai.Client(api_key=gemini_key)
            _t(f"Gemini 2.5 Flash ATIVO | pool de leitura K={k_leitura} (Tesseract fora)")
        except Exception as e:
            _t(f"Gemini indisponível ({str(e)[:80]}) — roda sem leitura")

    anexar_on = bool(gem) and k_attach > 0
    fila_pend = queue.Queue()
    fila_leit = queue.Queue()
    fila_anexar = queue.Queue()
    stop_desc = threading.Event()
    stop_dl = threading.Event()
    stop_dec = threading.Event()
    _lock = threading.Lock()
    resultados = []
    n_pend = {"n": 0}
    ativos_dl = {"n": 0, "pico": 0}
    ativos_le = {"n": 0, "pico": 0}
    ativos_an = {"n": 0, "pico": 0}

    # ---- ESTÁGIO 1: descoberta via API DIRETA (sem abrir popup) ----
    def _odonto_setup():
        """Login OdontoPrev (1 navegador): captura o Bearer token da sessão + lista
        os alvos. Depois disso a descoberta é HTTP puro (sem render de popup)."""
        _du, pwd = get_credentials_odonto()
        user = _odo_user or _du   # plano selecionado -> login = código da conta
        tok = {"v": None}
        alvos = []
        with sync_playwright() as pw:
            br, ctx, pg = login_odonto(pw, user, pwd)
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)

            def _grab(req):
                try:
                    if not tok["v"] and "credenciado.odontoprev.com.br" in req.url:
                        a = req.headers.get("authorization")
                        if a and a.lower().startswith("bearer"):
                            tok["v"] = a
                except Exception:
                    pass
            ctx.on("request", _grab)
            try:
                abrir_consultar_gtos(pg); consultar_periodo(pg, data)
                gtos = listar_gtos(pg)
                do_dia = [g for g in gtos if g.get("liberacao") == data] or gtos
                alvos = [g for g in do_dia if "REPASSE" in g["status"].upper()]
                if not tok["v"] and alvos:   # fallback: abre 1 GTO p/ disparar a API
                    try:
                        gp = abrir_gto(pg, alvos[0]["gto"], _refrescar=None)
                        gp.wait_for_timeout(1500)
                    except Exception:
                        pass
            finally:
                br.close()
        return tok["v"], alvos

    def descobridor_api(token, alvos):
        """Pra cada alvo, chama /v1/gto/imagens (nomes + contagem) e decide pendente.
        HTTP puro em paralelo (ThreadPool) -> sem popup, sem render, ~zero CPU."""
        from concurrent.futures import ThreadPoolExecutor
        sess = requests.Session()
        sess.headers.update({"Authorization": token or "", "User-Agent": "Mozilla/5.0",
                             "Origin": "https://credenciado.odontoprev.com.br",
                             "Referer": "https://credenciado.odontoprev.com.br/"})

        def _um(g):
            try:
                r = sess.get("https://gto-credenciado.odontoprev.com.br/v1/gto/imagens"
                             f"?numeroFicha={g['gto']}", timeout=20)
                imgs = r.json() if r.status_code == 200 else []
                nomes = {str(i.get("nomeArquivo", "")) for i in imgs}
                cnt = len(imgs)
            except Exception:
                nomes, cnt = set(), -1
            if cnt >= 2 or (cnt >= 0 and _ja_anexado_por_nos(nomes)):
                _t(f"[DESC] GTO {g['gto']}: {cnt} anexos -> completa, pula")
                return
            g["nome_norm"] = normaliza_nome(g["nome"])
            with _lock:
                n_pend["n"] += 1
            _t(f"[DESC] >>> PENDENTE {g['gto']} {g['nome']} ({cnt} anexos) -> fila")
            fila_pend.put(g)
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(_um, alvos))

    # ---- ESTÁGIO 2: download (PRORADIS) ----
    def baixador(wid, state, by_norm, tmp):
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = br.new_context(storage_state=state, locale="pt-BR", timezone_id="America/Sao_Paulo")
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)
            pg = ctx.new_page()
            pg.goto(f"{BASE}/admin_reports", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(800)
            while True:
                try:
                    g = fila_pend.get(timeout=2)
                except queue.Empty:
                    if stop_desc.is_set() and fila_pend.empty():
                        break
                    continue
                with _lock:
                    ativos_dl["n"] += 1; ativos_dl["pico"] = max(ativos_dl["pico"], ativos_dl["n"])
                try:
                    r = _baixa_um(pg, ctx, by_norm, g, tmp, data)
                except Exception as e:
                    r = {"gto": g["gto"], "nome": g["nome"], "status": "ERRO", "erro": str(e)[:120]}
                with _lock:
                    ativos_dl["n"] -= 1
                _t(f"[DL{wid}] {g['gto']} -> {r['status']} ({r.get('dt_dl', 0):.0f}s)")
                if gem is not None and r.get("status") == "BAIXADO" and r.get("_pac"):
                    fila_leit.put(r)         # entrega pro estágio de leitura
                else:
                    with _lock:
                        resultados.append(r)
            try:
                br.close()
            except Exception:
                pass

    # ---- ESTÁGIO 3: leitura (PRORADIS + Gemini) ----
    def leitor(wid, state):
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = br.new_context(storage_state=state, locale="pt-BR", timezone_id="America/Sao_Paulo")
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)
            pg = ctx.new_page()
            pg.goto(f"{BASE}/admin_reports", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(800)
            while True:
                try:
                    item = fila_leit.get(timeout=2)
                except queue.Empty:
                    if stop_dl.is_set() and fila_leit.empty():
                        break
                    continue
                with _lock:
                    ativos_le["n"] += 1; ativos_le["pico"] = max(ativos_le["pico"], ativos_le["n"])
                    em = _mem_mb()
                t0 = time.monotonic()
                try:
                    dec = _decidir(gem, pg, ctx, item["_pac"], item.get("_pasta"),
                                   review_dir=review_dir, gto=item["gto"], data_exame=data)
                except Exception as e:
                    dec = {"erro": str(e)[:100], "decisao": None, "anexos": 0,
                           "gto_exames": [], "plano_laudo_imgs": [], "plano_solicitacao": None}
                item["decisao"] = dec
                item["dt_decisao"] = time.monotonic() - t0
                with _lock:
                    ativos_le["n"] -= 1
                # auto/justificativa -> anexa (4º estágio); resto fica avisado (não fatura)
                anexa = bool(dec.get("justificativa") or dec.get("plano_solicitacao"))
                if anexar_on and anexa:
                    fila_anexar.put(item)
                else:
                    with _lock:
                        resultados.append(item)
                d = dec.get("decisao") or {}
                if dec.get("justificativa"):
                    solic = "JUSTIFICATIVA (solic dispensada)"
                elif dec.get("plano_solicitacao"):
                    solic = f"SOLIC={dec['plano_solicitacao']}"
                else:
                    solic = "solic->REVISÃO"
                _t(f"[DEC{wid}] {item['gto']} {item['nome'][:22]} | laudo+img={len(dec.get('plano_laudo_imgs', []))} "
                   f"| {solic} | conf={d.get('confianca')} batem={d.get('exames_batem')} "
                   f"({item['dt_decisao']:.0f}s, mem={em:.0f}MB)")
            try:
                br.close()
            except Exception:
                pass

    # ---- ESTÁGIO 4: anexação (OdontoPrev) ----
    def anexador(wid):
        _du, pwd = get_credentials_odonto()
        user = _odo_user or _du   # plano selecionado -> login = código da conta
        with sync_playwright() as pw:
            br, ctx, pg = login_odonto(pw, user, pwd)
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)
            try:
                abrir_consultar_gtos(pg); consultar_periodo(pg, data)
            except Exception as e:
                _t(f"[ANEX{wid}] consulta inicial falhou: {str(e)[:80]}")
            while True:
                try:
                    item = fila_anexar.get(timeout=2)
                except queue.Empty:
                    if stop_dec.is_set() and fila_anexar.empty():
                        break
                    continue
                pasta = item.get("_pasta")
                arquivos = ([os.path.join(pasta, f) for f in sorted(os.listdir(pasta))]
                            if pasta and os.path.isdir(pasta) else [])
                nomes = [os.path.basename(a) for a in arquivos]
                with _lock:
                    ativos_an["n"] += 1; ativos_an["pico"] = max(ativos_an["pico"], ativos_an["n"])
                if dry_run:
                    item["anexado"] = "DRY"
                    _t(f"[ANEX{wid}] [DRY] GTO {item['gto']} ANEXARIA {len(arquivos)}: {nomes}")
                else:
                    try:
                        gp = abrir_gto(pg, item["gto"])
                        res = upload_arquivos(gp, arquivos)
                        try:
                            gp.close()
                        except Exception:
                            pass
                        item["anexado"] = "OK" if res.get("ok") else "FALHOU"
                        item["upload"] = {k: res.get(k) for k in ("anexos_antes", "anexos_depois", "enviados", "ja_anexados")}
                        _t(f"[ANEX{wid}] GTO {item['gto']} -> {item['anexado']} "
                           f"({len(res.get('enviados', []))} enviados, {len(res.get('ja_anexados', []))} já tinha)")
                    except Exception as e:
                        item["anexado"] = "ERRO"; item["anexar_erro"] = str(e)[:120]
                        _t(f"[ANEX{wid}] GTO {item['gto']} ERRO {str(e)[:90]}")
                with _lock:
                    ativos_an["n"] -= 1
                    resultados.append(item)
            try:
                br.close()
            except Exception:
                pass

    # ---- 1) SETUP: PRORADIS (by_norm) e OdontoPrev (token+alvos) em paralelo ----
    _t(f"=== PIPELINE {data} | dl={m_download} leit={k_leitura if gem else 0} (descoberta via API) ===")
    setup = {}

    def _prorad_setup():
        email, password = get_credentials()
        with sync_playwright() as pw0:
            br0, ctx0, pg0 = _login_playwright(pw0, email, password)
            ctx0.set_default_timeout(45000); ctx0.set_default_navigation_timeout(60000)
            df = _get_relatorio_analitico(pg0, _convenios, _segmentos, data)
            setup["by_norm"] = _build_by_norm(df)
            setup["state"] = ctx0.storage_state()
            br0.close()

    def _odo_setup():
        setup["token"], setup["alvos"] = _odonto_setup()

    _ts = [threading.Thread(target=_prorad_setup), threading.Thread(target=_odo_setup)]
    for t in _ts:
        t.start()
    for t in _ts:
        t.join()
    by_norm, state = setup["by_norm"], setup["state"]
    token, alvos = setup.get("token"), setup.get("alvos", [])
    _t(f"PRORADIS by_norm={len(by_norm)} | OdontoPrev token={'ok' if token else 'FALHOU'} "
       f"| {len(alvos)} alvo(s)")
    tmp = tempfile.mkdtemp(prefix="_esteira_")

    # ---- 2) lança os pools (descoberta-API + download + decisão + anexação) ----
    tds = [threading.Thread(target=descobridor_api, args=(token, alvos), daemon=True)]
    tws = [threading.Thread(target=baixador, args=(i, state, by_norm, tmp), daemon=True)
           for i in range(1, m_download + 1)]
    tls = ([threading.Thread(target=leitor, args=(i, state), daemon=True)
            for i in range(1, k_leitura + 1)] if gem else [])
    tas = ([threading.Thread(target=anexador, args=(i,), daemon=True)
            for i in range(1, k_attach + 1)] if anexar_on else [])
    if anexar_on:
        _t(f"ANEXAÇÃO {'(DRY-RUN)' if dry_run else 'REAL'} ligada | K_attach={k_attach}")
    t_ini = time.monotonic()
    for t in tds + tws + tls + tas:
        t.start()
    for t in tds:
        t.join()
    t_desc = time.monotonic() - t_ini
    stop_desc.set()
    for t in tws:
        t.join()
    t_dl = time.monotonic() - t_ini
    stop_dl.set()
    for t in tls:
        t.join()
    t_dec = time.monotonic() - t_ini
    stop_dec.set()
    for t in tas:
        t.join()
    total = time.monotonic() - t_ini

    baixados = [r for r in resultados if r["status"] == "BAIXADO"]
    com_solic = [r for r in baixados if (r.get("decisao") or {}).get("plano_solicitacao")]
    com_justif = [r for r in baixados if (r.get("decisao") or {}).get("justificativa")]
    # painel das decisões (pro dry-run que você revisa)
    decisoes = []
    for r in baixados:
        dec = r.get("decisao") or {}
        d = dec.get("decisao") or {}
        if dec.get("justificativa"):
            cat = "justificativa"
        elif dec.get("plano_solicitacao"):
            cat = "auto"
        elif d.get("indice_solicitacao") is None:
            cat = "sem_solicitacao"
        else:
            cat = "revisao"
        decisoes.append({
            "gto": r["gto"], "paciente": r["nome"], "categoria": cat,
            "anexado": r.get("anexado"),
            "laudo_imgs": dec.get("plano_laudo_imgs", []),
            "solicitacao": dec.get("plano_solicitacao"),
            "anexar_solic": bool(dec.get("plano_solicitacao")),
            "justificativa": dec.get("justificativa"),
            "gto_exames": dec.get("gto_exames", []),
            "candidatos": dec.get("candidatos", []),
            "solic_idx": dec.get("solic_idx"),
            "gemini": {k: d.get(k) for k in ("tipo", "legivel", "paciente_lido",
                       "exames_lidos", "exames_batem", "confianca", "anexar", "motivo")},
            "erro": dec.get("erro"),
        })
    n_rev = len(baixados) - len(com_solic) - len(com_justif)
    anx = [r.get("anexado") for r in baixados if r.get("anexado")]
    resumo = {
        "data": data, "n_desc": n_desc, "m_download": m_download,
        "k_leitura": k_leitura if gem else 0, "gemini": bool(gem),
        "pendentes": n_pend["n"], "baixados": len(baixados),
        "outros": len(resultados) - len(baixados),
        "solic_auto": len(com_solic), "justificativa": len(com_justif), "revisao": n_rev,
        "anexar_on": anexar_on, "dry_run": dry_run,
        "anexado_ok": anx.count("OK"), "anexado_dry": anx.count("DRY"),
        "anexado_falhou": anx.count("FALHOU") + anx.count("ERRO"),
        "nao_faturadas": n_rev,
        "pico_download": ativos_dl["pico"], "pico_leitura": ativos_le["pico"],
        "pico_anexacao": ativos_an["pico"],
        "tempo_descoberta": round(t_desc), "tempo_ate_download": round(t_dl),
        "tempo_total": round(total), "decisoes": decisoes, "resultados": resultados,
    }
    _t(f"RESUMO: {resumo['baixados']}/{resumo['pendentes']} baixados | "
       f"{resumo['solic_auto']} solic-auto / {resumo['justificativa']} c-justificativa / "
       f"{resumo['revisao']} revisão | anexados ok={resumo['anexado_ok']} dry={resumo['anexado_dry']} "
       f"falhou={resumo['anexado_falhou']} | TOTAL={resumo['tempo_total']}s")
    return resumo
