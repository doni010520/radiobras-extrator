"""
fechar_dia.py — Orquestrador de ANEXO no OdontoPrev (fluxo diário real).

Disparo manual: escolhe a data e roda. Com dry_run=True só simula (não anexa).

Fase 1  OdontoPrev: lista GTOs do dia em "análise de repasse" (alvo).
Fase 2  PRORADIS (1 login): por paciente-alvo:
          - worklist -> baixa laudo + imagens (com fixes); completude c/ laudo combinado
          - prontuário -> baixa anexos -> acha GTO(pdf) + solicitação
          - lê justificativa (campo 49). Vazia -> decide solicitação (digitada+checks)
          - monta a lista de arquivos a anexar (regras do usuário)
Fase 3  OdontoPrev: anexa por GTO (ou simula em dry_run).

Regras de anexo:
  - laudo + imagens: sempre (se existirem).
  - justificativa preenchida            -> só laudo + imagens.
  - justificativa vazia + DIGITADA + checks OK -> + solicitação.
  - justificativa vazia + manuscrita/divergência/ausente -> laudo+imagens;
    solicitação => REVISÃO HUMANA (não anexa, fica na fila).
"""
import os
import re
import shutil
import tempfile
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright

from extrator_pacientes_analitico import BASE_URL as BASE, get_credentials
from extrator_arquivos import (
    _login_playwright, _get_relatorio_analitico, listar_worklist_por_pacientes,
    _processar_paciente, extrair_exame_status, slug,
)
from extrator_odontoprev import (
    login_odonto, get_credentials_odonto, abrir_consultar_gtos, consultar_periodo,
    listar_gtos, abrir_gto, ler_dados_gto, upload_arquivos, normaliza_nome,
    _anexos_nomes,
)
from extrair_anexos_dia import anexos_do_paciente
from gto_utils import is_gto_pdf, extrair_observacao
from solicitacao_utils import gto_solicitante, gto_exames, analisar_paciente, canon_exames
from laudo_utils import exames_pendentes_reais

STATUS_ALVO = "REPASSE"  # só GTOs em "análise de repasse"


def _ja_anexado_por_nos(nomes) -> bool:
    """True se a GTO já tem NOSSOS arquivos: pelo menos um LAUDO_* e um ENTREGA_*.
    Nomes deterministas da automação (prova de autoria) — anexos manuais usam
    outros nomes e NÃO casam aqui, então não são confundidos com 'já completa'."""
    tem_laudo = any(str(n).upper().startswith("LAUDO_") for n in nomes)
    tem_img = any(str(n).upper().startswith("ENTREGA_") for n in nomes)
    return tem_laudo and tem_img


def _solic_anexavel(ana: dict) -> bool:
    """Solicitação digitada com checks aceitáveis (dentista confere; exames != False)."""
    return (ana.get("status") == "DIGITADA"
            and ana.get("dentista_confere") is True
            and ana.get("exames_conferem") is not False)


def fechar_dia(data: str, convenios: list, segmentos: list,
               dry_run: bool = True, progress_cb=None, limite: int = 0,
               pular_completas: bool = True) -> dict:
    def log(m):
        print(m, flush=True)
        if progress_cb:
            try:
                progress_cb(m)
            except Exception:
                pass

    log(f"\n=== FECHAR DIA {data} (dry_run={dry_run}) ===")
    tmp = tempfile.mkdtemp(prefix="_fechar_")
    itens = []
    _t0 = time.monotonic()

    def _dt():
        return f"{time.monotonic() - _t0:.0f}s"
    try:
        ouser, opwd = get_credentials_odonto()

        # ── Fase 1: OdontoPrev — GTOs-alvo do dia ─────────────────────────────
        log("[1/3] OdontoPrev: listando GTOs do dia...")
        with sync_playwright() as pw:
            b, c, page = login_odonto(pw, ouser, opwd)
            try:
                gtos = []
                # até 2 tentativas: a tabela pode demorar a popular no servidor.
                for tentativa in range(2):
                    abrir_consultar_gtos(page)
                    consultar_periodo(page, data)
                    gtos = listar_gtos(page)
                    # só GTOs cuja liberação == dia pedido (garante o filtro certo)
                    do_dia = [g for g in gtos if g.get("liberacao") == data]
                    if do_dia:
                        gtos = do_dia
                        break
                    if tentativa == 0:
                        log("   (tabela vazia/atrasada — re-consultando o período...)")
                alvos = [g for g in gtos if STATUS_ALVO in g["status"].upper()]
                if limite:
                    alvos = alvos[:limite]
                log(f"   {len(gtos)} GTOs no dia | {len(alvos)} em 'análise de repasse' (alvo)")

                # Pré-checagem (mesma sessão): pula GTOs que JÁ têm nossos arquivos
                # (laudo+imagens), evitando re-baixar do PRORADIS num reprocessamento.
                # Loga GTO a GTO (nunca silenciosa) e falha rápido (_refrescar=None):
                # se uma GTO não abrir, processa normal em vez de re-consultar 30s.
                if pular_completas and alvos:
                    n = len(alvos)
                    log(f"   Conferindo anexos de {n} GTO(s) (pular já completas)...")
                    restantes = []
                    for i, g in enumerate(alvos, 1):
                        try:
                            gp = abrir_gto(page, g["gto"], _refrescar=None)
                            gp.wait_for_timeout(800)  # deixa a seção de anexos renderizar
                            nomes = _anexos_nomes(gp)
                            try:
                                gp.close()
                            except Exception:
                                pass
                        except Exception:
                            log(f"   [checagem {i}/{n}] GTO {g['gto']}: não abriu — processa normal")
                            restantes.append(g)
                            continue
                        if _ja_anexado_por_nos(nomes):
                            log(f"   [checagem {i}/{n}] GTO {g['gto']}: já completa — pular")
                            itens.append({
                                "gto": g["gto"], "nome_gto": g["nome"], "arquivos": [],
                                "status": "JA_ANEXADO", "solicitacao": None,
                                "revisao_humana": "",
                                "detalhe": "já tinha laudo+imagens nossos — pulada (não re-baixou)",
                            })
                        else:
                            log(f"   [checagem {i}/{n}] GTO {g['gto']}: pendente")
                            restantes.append(g)
                    log(f"   Checagem concluída: {len(alvos) - len(restantes)} pulada(s), "
                        f"{len(restantes)} a processar.")
                    alvos = restantes
            finally:
                b.close()
        if not gtos:
            log(f"   ATENÇÃO: 0 GTOs para {data}. Verifique a data ou tente de novo "
                "(pode ter sido lentidão do portal).")

        log(f"   Fase 1 (lista + checagem) concluída em {_dt()}.")

        # ── Fase 2: PRORADIS — montar arquivos por paciente ───────────────────
        _t_fase2 = time.monotonic()
        log(f"[2/3] PRORADIS: baixando p/ {len(alvos)} paciente(s)...")
        email, password = get_credentials()
        with sync_playwright() as pw:
            br, ctx, pg = _login_playwright(pw, email, password)
            try:
                df = _get_relatorio_analitico(pg, convenios, segmentos, data)
                cod_col = "Cód. Pac" if "Cód. Pac" in df.columns else df.columns[1]
                ped_col = "Pedido" if "Pedido" in df.columns else df.columns[6]
                nome_col = "Paciente" if "Paciente" in df.columns else df.columns[2]
                by_norm: dict = {}
                for _, r in df.iterrows():
                    nm = str(r[nome_col]).strip()
                    key = normaliza_nome(nm)
                    lst = by_norm.setdefault(key, [])
                    pac = next((p for p in lst if p["cod_pac"] == str(r[cod_col]).strip()), None)
                    if not pac:
                        pac = {"cod_pac": str(r[cod_col]).strip(), "nome": nm, "accessions": []}
                        lst.append(pac)
                    a = str(r[ped_col]).strip()
                    if a and a not in pac["accessions"]:
                        pac["accessions"].append(a)
                    if len(nm) > len(pac["nome"]):
                        pac["nome"] = nm

                for g in alvos:
                    _tp = time.monotonic()
                    item = {"gto": g["gto"], "nome_gto": g["nome"], "arquivos": [],
                            "solicitacao": None, "status": "", "detalhe": "",
                            "revisao_humana": ""}
                    cands = by_norm.get(g["nome_norm"], [])
                    if len(cands) > 1:
                        item["status"] = "AMBIGUO"
                        log(f"   [AMBÍGUO] GTO {g['gto']} {g['nome']!r}")
                        itens.append(item); continue

                    # (a) localizar paciente + worklist (laudo + imagens)
                    if cands:
                        pac = cands[0]
                        wl = listar_worklist_por_pacientes(pg, data, [pac["nome"]])
                    else:
                        # SEM_MATCH no analítico -> FALLBACK pelos LAUDOS. A GTO do
                        # OdontoPrev já é REDE UNNA, então o paciente é nosso mesmo que
                        # o exame esteja sob outro convênio/unidade no PRORADIS (caso
                        # ARTHUR: unidade LAURO, mas convênio fora do REDE UNNA, então
                        # não aparece no analítico financeiro — mas está nos laudos).
                        wl = listar_worklist_por_pacientes(pg, data, [g["nome"]])
                        accs = sorted({w["accession"] for w in wl if w.get("accession")})
                        if not accs:
                            item["status"] = "SEM_MATCH"
                            log(f"   [SEM MATCH] GTO {g['gto']} {g['nome']!r} "
                                "(nem no analítico nem nos laudos)")
                            itens.append(item); continue
                        pac = {"nome": g["nome"], "cod_pac": "WL" + accs[0],
                               "accessions": accs}
                        item["detalhe"] = "encontrado nos laudos (fora do analítico REDE UNNA)"
                        log(f"   [FALLBACK-LAUDOS] GTO {g['gto']} {g['nome']!r}: "
                            f"{len(accs)} exame(s) na worklist.")
                    log(f"   -> {pac['nome']} ({pac['cod_pac']}) | GTO {g['gto']}")

                    res = _processar_paciente(pg, ctx, pac, wl, tmp, data)
                    pasta = os.path.join(tmp, res["pasta"])
                    laudo_pdfs, imgs = [], []
                    if os.path.isdir(pasta):
                        for f in sorted(os.listdir(pasta)):
                            fp = os.path.join(pasta, f)
                            (laudo_pdfs if f.lower().endswith(".pdf") else imgs).append(fp)
                    tem_laudo = len(laudo_pdfs) > 0
                    n_imgs = res["imagens"]["qtd"]

                    # completude: exames "A Laudar" cobertos pelo laudo combinado?
                    exames_status = []
                    wl_by_acc = {w["accession"]: w for w in wl}
                    for acc in res.get("accessions", []):
                        w = wl_by_acc.get(acc)
                        if w:
                            for h in w["rows_html"]:
                                exames_status.append(extrair_exame_status(h))
                    clf = exames_pendentes_reais(laudo_pdfs, exames_status)
                    item["laudo_combinado"] = [f"{ex} ({st})" for ex, st in clf["incluidos"]]
                    item["exames_pendentes"] = [f"{ex} ({st})" for ex, st in clf["pendentes_reais"]]

                    # (b) prontuário: baixar anexos -> GTO pdf + solicitação
                    att_dir = os.path.join(tmp, "att_" + pac["cod_pac"])
                    os.makedirs(att_dir, exist_ok=True)
                    try:
                        lista = anexos_do_paciente(pg, pac["nome"], pac["cod_pac"])
                        cj = {ck["name"]: ck["value"] for ck in ctx.cookies()}
                        sess = requests.Session(); sess.cookies.update(cj)
                        sess.headers.update({"User-Agent": "Mozilla/5.0",
                                             "Referer": f"{BASE}/patients"})
                        for it in lista:
                            try:
                                rr = sess.get(it["url"], timeout=60)
                                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", it["filename"]) or it["id"]
                                with open(os.path.join(att_dir, safe), "wb") as f:
                                    f.write(rr.content)
                            except Exception:
                                pass
                    except Exception as e:
                        log(f"      [anexos] falha: {e}")

                    gtos_pdf = [os.path.join(att_dir, f) for f in os.listdir(att_dir)
                                if f.lower().endswith(".pdf") and is_gto_pdf(os.path.join(att_dir, f))]

                    # (c) justificativa (campo 49) — preenchida em alguma GTO?
                    justif_ok = False
                    for gp in gtos_pdf:
                        if extrair_observacao(gp).get("status") == "PREENCHIDO":
                            justif_ok = True
                            break

                    # (d) FILTRO DE EXAMES: anexar só os laudos cujos exames estão na
                    # GTO. Exames particulares (fora da GTO) NÃO vão pro OdontoPrev.
                    # As imagens (ENTREGA_*.jpg) não trazem o exame (nem no nome nem no
                    # cabeçalho/OCR) — então num caso MISTO vão p/ revisão humana.
                    gto_ex = set()
                    for _gp in gtos_pdf:
                        try:
                            gto_ex |= gto_exames(_gp)
                        except Exception:
                            pass

                    def _exame_laudo(p):
                        m = re.match(r"LAUDO_(.+?)_\d+_", os.path.basename(p))
                        return canon_exames(m.group(1)) if m else set()

                    laudos_cobertos, laudos_fora = [], []
                    if gto_ex:
                        for lp in laudo_pdfs:
                            cex = _exame_laudo(lp)
                            # exclui só se o exame foi IDENTIFICADO e está FORA da GTO
                            (laudos_fora if (cex and not (cex & gto_ex))
                             else laudos_cobertos).append(lp)
                    else:
                        laudos_cobertos = list(laudo_pdfs)  # GTO ilegível -> não filtra
                    misto = bool(laudos_fora)
                    item["gto_exames"] = sorted(gto_ex)
                    nota_misto = ""
                    if misto:
                        arquivos = list(laudos_cobertos)  # sem imagens (vão p/ revisão)
                        excl_ex = sorted({e for lp in laudos_fora for e in _exame_laudo(lp)})
                        item["laudos_excluidos"] = [os.path.basename(x) for x in laudos_fora]
                        item["exames_particulares"] = excl_ex
                        item["imagens_revisao"] = [os.path.basename(x) for x in imgs]
                        # grava nos campos PERSISTIDOS (detalhe/revisao_humana) p/ os relatórios
                        det = (f"Exames mistos — anexados da GTO: {sorted(gto_ex)}; "
                               f"particulares NÃO anexados: {excl_ex}; "
                               f"{len(imgs)} imagem(ns) p/ conferência manual")
                        item["detalhe"] = (item.get("detalhe", "") + " | " + det).strip(" |")
                        nota_misto = (f"imagens p/ conferência manual "
                                      f"(exames particulares fora da GTO: {excl_ex})")
                        log(f"      [EXAMES MISTOS] GTO={sorted(gto_ex)} | excluídos: "
                            f"{item['laudos_excluidos']} | {len(imgs)} imagem(ns) -> revisão")
                    else:
                        arquivos = list(laudos_cobertos) + list(imgs)
                    # 'tem_laudo' efetivo (p/ decisão de status) = há laudo COBERTO?
                    tem_laudo = len(laudos_cobertos) > 0

                    # decisão da solicitação
                    if justif_ok:
                        item["justificativa"] = "PREENCHIDA"
                        sol_dec = "nao_precisa"
                    elif gtos_pdf:
                        item["justificativa"] = "VAZIA"
                        gp = gtos_pdf[0]
                        import fitz
                        gto_text = "".join(p.get_text() for p in fitz.open(gp))
                        ana = analisar_paciente(att_dir, gp, gto_solicitante(gp),
                                                gto_exames(gp), gto_text=gto_text)
                        item["solic_analise"] = {k: ana.get(k) for k in
                                                 ("status", "solicitacao", "exames_conferem",
                                                  "dentista_confere")}
                        if _solic_anexavel(ana):
                            solp = os.path.join(att_dir,
                                                re.sub(r"[^A-Za-z0-9._-]+", "_", ana["solicitacao"]))
                            if os.path.exists(solp):
                                arquivos.append(solp)
                                item["solicitacao"] = ana["solicitacao"]
                            sol_dec = "anexar"
                        else:
                            sol_dec = "revisao_humana"
                            item["revisao_humana"] = (
                                f"solicitação {ana.get('status')} "
                                f"(dentista={ana.get('dentista_confere')}, "
                                f"exames={ana.get('exames_conferem')})")
                    else:
                        item["justificativa"] = "SEM_GTO_PDF"
                        sol_dec = "revisao_humana"
                        item["revisao_humana"] = "GTO/solicitação não localizada nos anexos"

                    if nota_misto:
                        item["revisao_humana"] = (
                            (item.get("revisao_humana", "") + " | " + nota_misto)
                            .strip(" |"))
                    item["arquivos"] = [os.path.basename(a) for a in arquivos]
                    item["_paths"] = arquivos
                    item["sol_decisao"] = sol_dec

                    if not tem_laudo:
                        item["status"] = "SEM_LAUDO"
                        _msg = ("nenhum laudo da GTO (todos particulares?)" if misto
                                else "sem laudo — não anexar")
                        item["detalhe"] = (item.get("detalhe", "") + " | " + _msg).strip(" |")
                    elif n_imgs == 0:
                        item["status"] = "SEM_IMAGENS"
                        item["detalhe"] = (item.get("detalhe", "") + " | sem imagens — revisar").strip(" |")
                    else:
                        item["status"] = "PRONTO"
                    log(f"      {item['status']} | laudo={tem_laudo} imgs={n_imgs} "
                        f"| solic={sol_dec} | arquivos={len(arquivos)} "
                        f"({time.monotonic() - _tp:.0f}s)")
                    itens.append(item)
            finally:
                br.close()
        log(f"   Fase 2 (PRORADIS) concluída em {time.monotonic() - _t_fase2:.0f}s "
            f"(total {_dt()}).")

        # ── Fase 3: OdontoPrev — anexar ───────────────────────────────────────
        prontos = [i for i in itens if i["status"] == "PRONTO" and i.get("_paths")]
        log(f"\n[3/3] OdontoPrev: {'[DRY-RUN] ' if dry_run else ''}"
            f"anexar em {len(prontos)} GTO(s)...")
        if not dry_run and prontos:
            with sync_playwright() as pw:
                b, c, page = login_odonto(pw, ouser, opwd)
                try:
                    abrir_consultar_gtos(page)
                    consultar_periodo(page, data)
                    def _refrescar():
                        consultar_periodo(page, data)
                    for item in prontos:
                        try:
                            gp = abrir_gto(page, item["gto"], _refrescar=_refrescar)
                            r = upload_arquivos(gp, item["_paths"])
                            item["upload"] = r
                            item["status"] = "ENVIADO" if r.get("ok") else "ERRO_UPLOAD"
                            nv, jx = len(r.get("enviados", [])), len(r.get("ja_anexados", []))
                            log(f"   [{item['status']}] GTO {item['gto']}: "
                                f"enviados={nv} ja_anexados={jx} "
                                f"anexos {r.get('anexos_antes')}->{r.get('anexos_depois')}")
                            try:
                                gp.close()
                            except Exception:
                                pass
                        except Exception as e:
                            item["status"] = "ERRO"
                            item["detalhe"] = str(e)
                            log(f"   [ERRO] GTO {item['gto']}: {e}")
                finally:
                    b.close()
        elif dry_run:
            for item in prontos:
                log(f"   [DRY] GTO {item['gto']} <- {item['arquivos']}")
        log(f"   Tempo total: {_dt()}.")

        # ── Relatório ─────────────────────────────────────────────────────────
        def cnt(s):
            return sum(1 for i in itens if i["status"] == s)
        for i in itens:
            i.pop("_paths", None)
        resumo = {
            "alvos": len(itens), "prontos": cnt("PRONTO"), "enviados": cnt("ENVIADO"),
            "ja_anexados": cnt("JA_ANEXADO"),
            "sem_match": cnt("SEM_MATCH"), "ambiguos": cnt("AMBIGUO"),
            "sem_laudo": cnt("SEM_LAUDO"), "sem_imagens": cnt("SEM_IMAGENS"),
            "erros": cnt("ERRO") + cnt("ERRO_UPLOAD"),
            "solic_anexada": sum(1 for i in itens if i.get("solicitacao")),
            "revisao_humana": sum(1 for i in itens if i.get("revisao_humana")),
        }
        log(f"\n[OK] {resumo}")
        return {"gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data": data, "dry_run": dry_run, "resumo": resumo, "itens": itens}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import json
    import sys
    from config import CONVENIOS, SEGMENTOS
    DATA = sys.argv[1] if len(sys.argv) > 1 else "03/06/2026"
    DRY = "--go" not in sys.argv
    # --full: reprocessa TODAS as GTOs (não pula as já completas). Útil se uma
    # execução anterior foi interrompida no meio de um upload.
    PULAR = "--full" not in sys.argv
    LIM = 0
    for a in sys.argv:
        if a.startswith("--limite="):
            LIM = int(a.split("=")[1])
    rel = fechar_dia(DATA, CONVENIOS, SEGMENTOS, dry_run=DRY, limite=LIM,
                     pular_completas=PULAR)
    out = f"_fechar_{DATA.replace('/', '')}.json"
    json.dump(rel, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("relatorio salvo em", out)
