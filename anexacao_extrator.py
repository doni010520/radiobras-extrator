"""
Varredura SÓ-LEITURA do estado de anexação/faturamento das GTOs no portal
RedeUna/OdontoPrev — 3 unidades. Para cada GTO do período: lê o status e a
quantidade de anexos e classifica. Não anexa nada.

Categorias (mutuamente exclusivas, por prioridade):
  CANCELADA   -> status "GTO cancelada" / "não autorizada"
  LIBERADA    -> status "Senha Liberada" (liberada para assinatura, aguardando)
  FATURADA    -> >= 2 anexos (laudo + entrega)
  A_FATURAR   -> exatamente 1 anexo (falta o 2º para faturar)
  SEM_ANEXO   -> 0 anexos

100% determinístico (Playwright). Sem segredos no código (usa ODONTOPREV_USER/
PASSWORD do ambiente; a senha das 3 contas é a mesma).
"""
import os
import re
import time

from extrator_odontoprev import (
    login_odonto, get_credentials_odonto, abrir_consultar_gtos,
    listar_gtos, abrir_gto, _anexos_count,
)
from glosa_extrator import CONTAS  # mesmas 3 unidades/rótulos


def categoria(status: str, qtd: int) -> str:
    st = (status or "").lower()
    if "cancel" in st or "não autoriz" in st or "nao autoriz" in st:
        return "CANCELADA"
    if "senha liberada" in st or "liberad" in st:
        return "LIBERADA"
    if qtd >= 2:
        return "FATURADA"
    if qtd == 1:
        return "A_FATURAR"
    return "SEM_ANEXO"


def consultar_intervalo(page, de, ate):
    """Filtro Período = intervalo (DD/MM/AAAA - DD/MM/AAAA) e consulta."""
    page.mouse.click(1100, 600)
    page.wait_for_timeout(600)
    page.click("text=Período", timeout=8000)
    page.wait_for_timeout(1200)
    fld = None
    try:
        fld = page.query_selector(
            "xpath=//*[contains(normalize-space(.),'Selecione o per')]"
            "/ancestor-or-self::div[contains(@class,'v-input')][1]//input")
    except Exception:
        fld = None
    if not fld:
        for el in page.query_selector_all("input[type='text'], input:not([type])"):
            try:
                if el.is_visible():
                    fld = el
                    break
            except Exception:
                continue
    fld.click()
    page.wait_for_timeout(300)
    fld.type(f"{de} - {ate}", delay=50)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    page.click("button:has-text('CONSULTAR')")
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(6000)


def varrer_unidade(pw, conta, label, de, ate, limite=0, log=print) -> dict:
    """Varre as GTOs do período de uma unidade. Só abre (para contar anexos) as
    que estão em repasse; Liberada/Cancelada saem direto do status da lista."""
    user, pwd = conta, get_credentials_odonto()[1]
    b, c, page = login_odonto(pw, user, pwd)
    rows = []

    def _refr():
        consultar_intervalo(page, de, ate)
    try:
        abrir_consultar_gtos(page)
        consultar_intervalo(page, de, ate)
        gtos = listar_gtos(page)
        if limite:
            gtos = gtos[:limite]
        log(f"[{label}] {len(gtos)} GTO(s) no período {de}–{ate}")
        falhas = 0
        for i, g in enumerate(gtos, 1):
            rec = {"conta": conta, "unidade": label, "gto": g["gto"],
                   "paciente": g["nome"], "liberacao": g.get("liberacao", ""),
                   "status": g["status"], "qtd_anexos": -1, "categoria": ""}
            cat0 = categoria(g["status"], 0)
            if cat0 in ("CANCELADA", "LIBERADA"):
                # não precisa abrir — status já decide
                rec["categoria"] = cat0
                rec["qtd_anexos"] = 0
            else:
                try:
                    gp = abrir_gto(page, g["gto"], _refrescar=_refr)
                    gp.wait_for_timeout(500)
                    cnt = _anexos_count(gp)
                    try:
                        gp.close()
                    except Exception:
                        pass
                    qtd = cnt if cnt >= 0 else 0
                    rec["qtd_anexos"] = qtd
                    rec["categoria"] = categoria(g["status"], qtd)
                    falhas = 0
                except Exception as e:
                    rec["categoria"] = "ERRO"
                    rec["erro"] = str(e)[:120]
                    falhas += 1
            rows.append(rec)
            if i % 20 == 0 or i == len(gtos):
                from collections import Counter
                cc = Counter(r["categoria"] for r in rows)
                log(f"[{label}] {i}/{len(gtos)} | {dict(cc)}")
            if falhas >= 6:
                log(f"[{label}] muitas falhas — re-login…")
                try:
                    b.close()
                except Exception:
                    pass
                b, c, page = login_odonto(pw, user, pwd)
                abrir_consultar_gtos(page)
                consultar_intervalo(page, de, ate)
                falhas = 0
    finally:
        try:
            b.close()
        except Exception:
            pass
    return {"conta": conta, "label": label, "de": de, "ate": ate, "gtos": rows}
