"""Coleta o DESFECHO na RedeUna das guias que NÓS faturamos.

Âncora: db.guias_faturadas_por_nos (ExecucaoItem.faturado). Por guia consulta o
Demonstrativo de Pagamento (pago/glosado/repasse); as glosadas são enriquecidas com
motivo + como-recursar (relatório de glosa) + estado do recurso + prazo (120d orto /
90d demais a contar do repasse). 100% leitura; nenhuma escrita no portal.
"""
import os
import re
from datetime import date, datetime

from extrator_odontoprev import login_odonto
from glosa_extrator import (_abrir_topo, _clicar_subitem, _btn_por_texto, _num_brl,
                            _demo_set_guia, checar_recurso, extrair_unidade)
import db
import desfecho as _d


# Orientação "Como Recursar?" por código de glosa — texto padronizado do Manual do
# Credenciado (capturado do próprio Relatório de Glosa). Fallback genérico no fim.
COMO_RECURSAR = {
    "3052": "Enviar nova imagem da GTO (nítida, completa, sem cortes) em Recurso de "
            "Glosa via Portal Rede UNNA; conferir pedido em papel timbrado, com nome do "
            "beneficiário, dente/região, data e carimbo do dentista.",
    "1733": "Recuperação de valores por pagamento indevido — em geral NÃO recursável; "
            "conferir no Recurso de Glosa se a guia aceita recurso.",
    "2908": "Reanálise já efetuada de forma incorreta — o recurso anterior foi recusado; "
            "revisar a documentação antes de tentar de novo.",
}
COMO_RECURSAR_GENERICO = ("Abrir Recurso de Glosa via Portal Rede UNNA na guia e seguir "
                          "a orientação do relatório para o motivo específico.")

_DT_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")


def _parse_br(dstr):
    try:
        return datetime.strptime(dstr, "%d/%m/%Y").date()
    except Exception:
        return None


def consultar_demo_repasse(page, guia) -> dict:
    """Como glosa_extrator.consultar_demonstrativo, mas captura também a DATA DO
    REPASSE (necessária pro prazo). {tem_dados, bruto, glosado, pago, data_repasse}."""
    out = {"tem_dados": False, "bruto": None, "glosado": None, "pago": False,
           "data_repasse": None}
    if not _demo_set_guia(page, guia):
        return out
    page.mouse.move(1100, 650); page.wait_for_timeout(150)
    btn = _btn_por_texto(page, "CONSULTAR")
    if btn:
        try:
            btn.click(timeout=6000)
        except Exception:
            btn.click(force=True)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    corpo = re.sub(r"\s+", " ", page.inner_text("body") or "")
    mb = re.search(r"bruto[:\s]*R\$\s*([\d\.,]+)", corpo, re.I)
    mg = re.search(r"glosado[:\s]*R\$\s*([\d\.,]+)", corpo, re.I)
    out["bruto"] = _num_brl(mb.group(1)) if mb else None
    out["glosado"] = _num_brl(mg.group(1)) if mg else None
    sem_pg = "não há dados" in corpo.lower() or "nao ha dados" in corpo.lower()
    out["tem_dados"] = bool(out["bruto"] and out["bruto"] > 0)
    out["pago"] = bool(out["tem_dados"] and not sem_pg)
    # data do repasse: procura perto de 'repasse/pagamento/crédito'
    md = re.search(r"(?:repasse|pagamento|cr[eé]dito|compet[eê]ncia)[^\d]{0,30}(\d{2}/\d{2}/\d{4})",
                   corpo, re.I)
    if not md:
        md = _DT_RE.search(corpo)   # fallback: primeira data da tela
    out["data_repasse"] = md.group(1) if md else None
    nb = _btn_por_texto(page, "NOVA BUSCA")
    if nb:
        try:
            nb.click(force=True)
        except Exception:
            pass
        page.wait_for_timeout(1000)
    return out


def _como_recursar(glosa_cod):
    return COMO_RECURSAR.get(glosa_cod, COMO_RECURSAR_GENERICO)


def extrair_desfechos_conta(pw, conta, unidade, guias, dia_str, hoje=None, log=print) -> list:
    """Desfecho das `guias` (lista de dicts {gto,paciente,dia_faturado}) de UMA conta.
    dia_str = data-fim p/ o relatório de glosa (period até hoje). Retorna itens prontos
    pra db.salvar_desfechos."""
    hoje = hoje or date.today()
    senha = db.get_portal_senha(conta)

    # 1) glosa da unidade: SÓ o PDF (motivo/código por ficha). O recurso NÃO é checado
    # aqui (o extrair_unidade checaria as N glosadas do período inteiro) — checamos
    # depois SÓ as nossas glosadas, que são poucas. Ganho grande em escala.
    glosa_por_ficha = {}
    try:
        r = extrair_unidade(pw, conta, unidade, dia_str, "_diag_glosa",
                            checar_recursos=False, checar_demonstrativo=False, log=log)
        for e in r.get("eventos", []):
            glosa_por_ficha.setdefault(str(e["ficha"]), e)  # 1º evento por ficha
    except Exception as e:
        log(f"[{unidade}] glosa bulk falhou: {str(e)[:80]}")

    # 2) Demonstrativo por guia -> status financeiro
    itens = []
    b, c, page = login_odonto(pw, conta, senha)
    try:
        _abrir_topo(page, "Financeiro"); _clicar_subitem(page, "DEMONSTRATIVO")
        page.wait_for_timeout(1200)
        page.mouse.move(1100, 400); page.mouse.click(1100, 400); page.wait_for_timeout(500)
        for i, g in enumerate(guias, 1):
            gto = str(g["gto"])
            try:
                demo = consultar_demo_repasse(page, gto)
            except Exception:
                demo = None
            status = _d.classificar_desfecho(cancelada=False, demo=demo)
            itens.append({"conta": conta, "unidade": unidade, "gto": gto,
                          "paciente": g.get("paciente"), "dia_faturado": g.get("dia_faturado"),
                          "status": status,
                          "valor_bruto": (demo or {}).get("bruto"),
                          "valor_glosado": (demo or {}).get("glosado"),
                          "valor_pago": ((demo or {}).get("bruto") or 0) - ((demo or {}).get("glosado") or 0)
                          if demo and demo.get("tem_dados") else None,
                          "data_repasse": (demo or {}).get("data_repasse")})
            if i % 10 == 0 or i == len(guias):
                log(f"[{unidade}]   demonstrativo {i}/{len(guias)}")
    finally:
        try:
            b.close()
        except Exception:
            pass

    # 3) Recurso SÓ das NOSSAS glosadas (poucas) — recursável x sem-glosado
    minhas_glosadas = [it["gto"] for it in itens if it["status"] == "GLOSADA"]
    recurso = {}
    if minhas_glosadas:
        log(f"[{unidade}] checando recurso de {len(minhas_glosadas)} glosada(s) nossa(s)...")
        b, c, page = login_odonto(pw, conta, senha)
        try:
            _abrir_topo(page, "Recurso de Glosa"); _clicar_subitem(page, "RECURSO DE GLOSA")
            for gto in minhas_glosadas:
                try:
                    recurso[gto] = checar_recurso(page, gto)
                except Exception:
                    recurso[gto] = "INDEFINIDO"
                try:
                    _abrir_topo(page, "Recurso de Glosa"); _clicar_subitem(page, "RECURSO DE GLOSA")
                except Exception:
                    pass
        finally:
            try:
                b.close()
            except Exception:
                pass

    # 4) enriquece as glosadas com motivo + como-recursar + estado + prazo
    for item in itens:
        if item["status"] != "GLOSADA":
            continue
        gto = item["gto"]
        ev = glosa_por_ficha.get(gto, {})
        evtxt = f"{ev.get('evento', '')} {ev.get('glosa_motivo', '')}"
        orto = _d.eh_ortodontia(evtxt)
        pr = _d.prazo_recurso(_parse_br(item["data_repasse"]), orto, hoje)
        item.update({
            "glosa_cod": ev.get("glosa_cod", ""),
            "glosa_motivo": ev.get("glosa_motivo", ""),
            "como_recursar": _como_recursar(ev.get("glosa_cod", "")),
            "recurso_estado": "PRESCRITO" if pr["prescrito"] else recurso.get(gto, "NAO_CHECADO"),
            "ortodontia": orto,
            "prazo_limite": pr["data_limite"].strftime("%d/%m/%Y") if pr["data_limite"] else "",
            "prazo_dias": pr["dias_restantes"], "prescrito": pr["prescrito"],
        })
    return itens
