"""
Ciclo completo: PRORADIS (baixa imagens+laudos) -> OdontoPrev (upload na GTO).

Regra de negócio (definida pelo usuário):
  - GTOs com status 'Senha Liberada'  -> NÃO recebem anexo (ignoradas).
  - GTOs com status 'encaminhada para análise de repasse' -> recebem os
    arquivos, MESMO que já tenham anexos (sem trava de idempotência).

Para um dia:
  1. OdontoPrev: lista GTOs do dia e filtra as 'análise de repasse'.
  2. PRORADIS: relatório do dia; casa cada GTO-alvo (por nome) e baixa
     imagens + laudos.
  3. OdontoPrev: reabre cada GTO-alvo e faz upload. Com dry_run=True só simula.
"""

import os
import shutil
import tempfile
from datetime import datetime

from playwright.sync_api import sync_playwright

from extrator_arquivos import (
    _login_playwright, _get_relatorio_analitico, listar_worklist_por_pacientes,
    _processar_paciente, get_credentials, extrair_exame_status,
)
from laudo_utils import exames_pendentes_reais

# Status de exame que indicam laudo ATIVAMENTE pendente -> bloqueia o upload.
STATUS_PENDENTE = {"LAUDANDO", "A LAUDAR"}
from extrator_odontoprev import (
    login_odonto, get_credentials_odonto, abrir_consultar_gtos, consultar_periodo,
    listar_gtos, abrir_gto, ler_dados_gto, upload_arquivos, normaliza_nome,
)

# Anexa apenas nas GTOs cujo status indica "análise de repasse".
STATUS_ALVO = "REPASSE"


def ciclo_dia(data: str, convenios: list, segmentos: list,
              progress_cb=None, dry_run: bool = False) -> dict:
    """data: 'DD/MM/YYYY'. Retorna relatório dict."""

    def log(msg: str):
        print(msg)
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    log(f"\n=== CICLO COMPLETO {data} (dry_run={dry_run}) ===")
    itens = []
    tmp = tempfile.mkdtemp(prefix="_ciclo_")

    try:
        ouser, opwd = get_credentials_odonto()

        # ── Fase 1: OdontoPrev — filtrar GTOs em 'análise de repasse' ────────────
        log("[1/3] OdontoPrev: listando GTOs do dia...")
        with sync_playwright() as pw:
            b, c, page = login_odonto(pw, ouser, opwd)
            try:
                abrir_consultar_gtos(page)
                consultar_periodo(page, data)
                gtos = listar_gtos(page)
            finally:
                b.close()
        alvos = [g for g in gtos if STATUS_ALVO in g["status"].upper()]
        ignoradas = len(gtos) - len(alvos)
        log(f"   {len(gtos)} GTOs no dia | {len(alvos)} em 'análise de repasse' (alvo)"
            f" | {ignoradas} ignoradas (ex.: 'Senha Liberada').")

        # ── Fase 2: PRORADIS — casar e baixar as GTOs-alvo ───────────────────────
        prontos = []
        if alvos:
            log("[2/3] PRORADIS: casando e baixando arquivos das GTOs-alvo...")
            email, password = get_credentials()
            with sync_playwright() as pw:
                br, ctx, pg = _login_playwright(pw, email, password)
                try:
                    df = _get_relatorio_analitico(pg, convenios, segmentos, data)
                    cod_col = "Cód. Pac" if "Cód. Pac" in df.columns else df.columns[1]
                    pedido_col = "Pedido" if "Pedido" in df.columns else df.columns[6]
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
                        a = str(r[pedido_col]).strip()
                        if a and a not in pac["accessions"]:
                            pac["accessions"].append(a)
                        if len(nm) > len(pac["nome"]):
                            pac["nome"] = nm

                    for g in alvos:
                        item = {"gto": g["gto"], "nome_gto": g["nome"], "arquivos": [],
                                "status": "", "detalhe": ""}
                        cands = by_norm.get(g["nome_norm"], [])
                        if not cands:
                            item["status"] = "SEM_MATCH"
                            item["detalhe"] = "paciente não encontrado no PRORADIS"
                            log(f"   [SEM MATCH] GTO {g['gto']} {g['nome']!r}")
                            itens.append(item); continue
                        if len(cands) > 1:
                            item["status"] = "AMBIGUO"
                            item["detalhe"] = f"{len(cands)} pacientes com mesmo nome — revisar"
                            log(f"   [AMBÍGUO] GTO {g['gto']} {g['nome']!r}")
                            itens.append(item); continue
                        pac = cands[0]
                        wl = listar_worklist_por_pacientes(pg, data, [pac["nome"]])
                        res = _processar_paciente(pg, ctx, pac, wl, tmp, data)
                        pasta = os.path.join(tmp, res["pasta"])
                        arqs = ([os.path.join(pasta, f) for f in sorted(os.listdir(pasta))]
                                if os.path.isdir(pasta) else [])
                        item.update({"cod_pac": pac["cod_pac"], "nome_proradis": pac["nome"],
                                     "arquivos": arqs, "status_proradis": res["status"]})

                        # Completude: exames ativamente pendentes (Laudando / A Laudar),
                        # EXCETO os já incluídos no laudo combinado da panorâmica.
                        wl_by_acc = {w["accession"]: w for w in wl}
                        exames_status = []
                        for acc in res.get("accessions", []):
                            w = wl_by_acc.get(acc)
                            if not w:
                                continue
                            for h in w["rows_html"]:
                                exames_status.append(extrair_exame_status(h))
                        laudo_pdfs = [a for a in arqs if a.lower().endswith(".pdf")]
                        clf = exames_pendentes_reais(laudo_pdfs, exames_status)
                        pend_ex = {f"{ex or '?'}: {st}" for ex, st in clf["pendentes_reais"]}
                        for ex, st in clf["incluidos"]:
                            log(f"      [combinado] {ex} estava '{st}', já incluído no laudo da panorâmica")
                            item.setdefault("laudo_combinado", []).append(
                                f"{ex} ({st}) incluído no laudo da panorâmica")
                        motivos = []
                        if res["status"] != "OK":
                            motivos.extend(res.get("pendencias") or ["sem laudo disponível"])
                        if pend_ex:
                            motivos.append("exames pendentes (" + "; ".join(sorted(pend_ex)) + ")")

                        if not arqs:
                            item["status"] = "SEM_ARQUIVOS"
                            item["detalhe"] = "PRORADIS não retornou imagens/laudos"
                            log(f"   [SEM ARQUIVOS] GTO {g['gto']} <-> {pac['nome']}")
                        elif motivos:
                            item["status"] = "INCOMPLETO"
                            item["detalhe"] = " | ".join(motivos)
                            log(f"   [INCOMPLETO] GTO {g['gto']} <-> {pac['nome']}: {item['detalhe']}")
                        else:
                            item["status"] = "PRONTO"
                            prontos.append(item)
                            log(f"   [PRONTO] GTO {g['gto']} <-> {pac['nome']} ({len(arqs)} arquivos)")
                        itens.append(item)
                finally:
                    br.close()
        else:
            log("[2/3] Nenhuma GTO em análise de repasse — pulando PRORADIS.")

        # ── Fase 3: OdontoPrev — upload das prontas ──────────────────────────────
        log(f"[3/3] OdontoPrev: enviando para {len(prontos)} GTO(s)"
            + (" [DRY-RUN]" if dry_run else "") + "...")
        if prontos:
            with sync_playwright() as pw:
                b, c, page = login_odonto(pw, ouser, opwd)
                try:
                    abrir_consultar_gtos(page)
                    consultar_periodo(page, data)
                    for item in prontos:
                        try:
                            gp = abrir_gto(page, item["gto"])
                            dados = ler_dados_gto(gp)
                            item["carteirinha"] = dados.get("carteirinha", "")
                            if dry_run:
                                item["status"] = "DRY_RUN"
                                item["detalhe"] = f"subiria {len(item['arquivos'])} arquivos (anexos atuais: {dados.get('anexos', '?')})"
                                log(f"   [DRY-RUN] GTO {item['gto']}: subiria {len(item['arquivos'])} arquivos")
                            else:
                                r = upload_arquivos(gp, item["arquivos"])
                                if r["ok"]:
                                    item["status"] = "ENVIADO"
                                    item["detalhe"] = f"{len(item['arquivos'])} arquivos (anexos {r['anexos_antes']}->{r['anexos_depois']})"
                                    log(f"   [ENVIADO] GTO {item['gto']}: {len(item['arquivos'])} arquivos")
                                else:
                                    item["status"] = "ERRO_UPLOAD"
                                    item["detalhe"] = f"anexos {r['anexos_antes']}->{r['anexos_depois']}"
                                    log(f"   [ERRO UPLOAD] GTO {item['gto']}")
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

        # ── Relatório ────────────────────────────────────────────────────────────
        def cnt(s): return sum(1 for i in itens if i["status"] == s)
        resumo = {
            "alvos": len(itens),
            "enviados": cnt("ENVIADO"),
            "pulados": cnt("PULADO_JA_TINHA"),
            "sem_match": cnt("SEM_MATCH"),
            "ambiguos": cnt("AMBIGUO"),
            "sem_arquivos": cnt("SEM_ARQUIVOS"),
            "incompletos": cnt("INCOMPLETO"),
            "erros": cnt("ERRO") + cnt("ERRO_UPLOAD"),
            "dry_run": cnt("DRY_RUN"),
        }
        log(f"\n[OK] Ciclo concluído: {resumo}")
        for i in itens:
            i["arquivos"] = [os.path.basename(a) for a in i.get("arquivos", [])]
        return {
            "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": data, "dry_run": dry_run,
            "resumo": resumo, "itens": itens,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
