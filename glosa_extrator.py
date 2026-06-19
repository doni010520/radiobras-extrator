"""
Extração de GLOSAS e situação de RECURSO no Portal RedeUna/OdontoPrev.

Fontes (mapeadas em 18/06/2026):
  Recurso de Glosa > Relatório de Glosa  -> PDF detalhado por guia/evento/motivo
  Recurso de Glosa > Recurso de glosa    -> por guia: recursável (mostra opções)
                                            ou "não apresenta eventos glosados".

Fluxo por unidade:
  login -> Relatório de Glosa -> escolhe data no calendário (1º dia do mês até o
  dia escolhido) -> GERAR RELATÓRIO -> GERAR RELATÓRIO EM PDF -> parse do PDF.
  Depois, para cada guia distinta, checa a tela de Recurso (recursável x não).

100% determinístico (Playwright + PyMuPDF). Sem LLM. Sem segredos no código
(usa ODONTOPREV_USER/PASSWORD do ambiente; a senha das 3 contas é a mesma).
"""
import os
import re
import time

import fitz  # PyMuPDF

from extrator_odontoprev import login_odonto, get_credentials_odonto

# Unidades (mesma senha p/ as 3 contas).
CONTAS = [("388336", "Centro, Lauro, Periperi e Itaigara"),
          ("397950", "Tancredo"), ("410923", "Camacari")]

# ── Parser do PDF "Relatório de Glosas" (layout rotacionado em faixas) ──────────
_RUIDO = re.compile(
    r"^(Relat[oó]rio de Glosas|P[aá]gina \d+ de \d+|\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}"
    r"|Ficha|Benefici[aá]rio|Evento|Dente /|Regi[aã]o|Data de|Realiza[cç][aã]o|Glosa"
    r"|Quando Ocorre\?|Como Evitar\?|Como Recursar\?|Nome:.*|C[oó]digo:.*|CNPJ:.*"
    r"|CPF:|INSS:|null)$",
    re.I,
)
_RE_FICHA = re.compile(r"^\d{8,10}$")
_RE_BENEF = re.compile(r"(\d{6,})\s*-\s*([A-Za-zÀ-Úà-ú'’ ]+?)\s+(?=\d{2}\.\d{3}\.\d{3}\s*-)")
_RE_EVENTO = re.compile(r"(\d{2}\.\d{3}\.\d{3})\s*-\s*(.+?)\s+(?=\d{3,4}\s*-\s)")
_ANCORA_FIM = (r"Quando|Esse relat|Sempre|Ocorre|Verificar|Certifique|Para essa"
               r"|Todos os|Solicite|Caso ")
_RE_GLOSA = re.compile(r"\b(\d{4})\s*-\s*(.+?)\s+(?=" + _ANCORA_FIM + r"|$)")

# Códigos de glosa que NÃO são recursáveis (recuperação financeira / cancelamento).
# A recursabilidade definitiva vem da tela de Recurso; isto é só um fallback.
GLOSAS_NAO_RECURSAVEIS = {"1733"}


def _carregar_linhas(pdf_path):
    doc = fitz.open(pdf_path)
    linhas = []
    for pg in doc:
        for ln in pg.get_text().splitlines():
            ln = re.sub(r"\s+", " ", ln).strip()
            if ln and not _RUIDO.match(ln):
                linhas.append(ln)
    doc.close()
    return linhas


def _segmentar(linhas):
    regs, atual = [], None
    for ln in linhas:
        if _RE_FICHA.match(ln):
            if atual:
                regs.append(atual)
            atual = [ln]
        elif atual is not None:
            atual.append(ln)
    if atual:
        regs.append(atual)
    return regs


def _parse_reg(reg):
    ficha = reg[0]
    blob = re.sub(r"\s+", " ", ficha + " " + " ".join(reg[1:])).strip()
    resto = blob[len(ficha):].strip()
    benef = _RE_BENEF.search(resto)
    evento = _RE_EVENTO.search(resto, benef.end() if benef else 0)
    glosa = _RE_GLOSA.search(resto, evento.end() if evento else (benef.end() if benef else 0))
    ev_desc = evento.group(2).strip() if evento else ""
    ev_desc = re.sub(r"\s+[A-Z]{2,6}$", "", ev_desc).strip()  # tira token de região no fim
    return {
        "ficha": ficha,
        "benef_cod": benef.group(1) if benef else "",
        "paciente": (benef.group(2).strip() if benef else ""),
        "evento_cod": evento.group(1) if evento else "",
        "evento": ev_desc,
        "glosa_cod": glosa.group(1) if glosa else "",
        "glosa_motivo": (glosa.group(2).strip() if glosa else ""),
    }


def parse_glosa_pdf(pdf_path) -> list:
    """Lê o PDF do Relatório de Glosas e devolve eventos (dedup por
    ficha+evento+glosa). Cada item: ficha, paciente, evento, glosa_cod, glosa_motivo."""
    regs = _segmentar(_carregar_linhas(pdf_path))
    vistos, out = set(), []
    for r in regs:
        e = _parse_reg(r)
        if not e["glosa_cod"]:
            continue
        k = (e["ficha"], e["evento_cod"], e["glosa_cod"])
        if k in vistos:
            continue
        vistos.add(k)
        out.append(e)
    return out


# ── Navegação no portal ────────────────────────────────────────────────────────
def _abrir_topo(page, nome_topo, tentativas=20):
    for _ in range(tentativas):
        for el in page.query_selector_all(".c-menu-itens.c-subMenu"):
            t = re.sub(r"\s+", " ", (el.inner_text() or "")).strip()
            if t.lower().startswith(nome_topo.lower()):
                box = el.bounding_box()
                if box:
                    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                    page.mouse.move(cx, cy)
                    page.wait_for_timeout(600)
                    page.mouse.click(cx, cy)
                    page.wait_for_timeout(1500)
                    return True
        page.wait_for_timeout(800)
    return False


def _clicar_subitem(page, trecho):
    for el in page.query_selector_all("li.a-item-submenu, .a-item-submenu"):
        if trecho.upper() in (el.inner_text() or "").upper():
            el.click()
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(3000)
            return True
    return False


# Abreviações de mês usadas no cabeçalho do mx-datepicker (ex.: "Jun2026").
_MES_ABREV = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
              "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}


def _ler_mes_ano(label_el):
    """Lê '(mês, ano)' do cabeçalho de um calendário (ex.: 'Jun2026')."""
    t = re.sub(r"\s+", "", (label_el.inner_text() or "")).lower()
    m = re.match(r"([a-zç]{3})(\d{4})", t)
    if not m:
        return None, None
    return _MES_ABREV.get(m.group(1)), int(m.group(2))


def _selecionar_data_calendario(page, dia_str):
    """mx-datepicker (range, mostra 2 meses, rótulo abreviado tipo 'Jun2026').
    Navega até o mês/ano alvo e clica o dia DENTRO do calendário certo."""
    dd, mm, aaaa = [int(x) for x in dia_str.split("/")]
    fld = None
    for el in page.query_selector_all("input"):
        try:
            if el.is_visible():
                fld = el
                break
        except Exception:
            continue
    if not fld:
        return False
    fld.click()
    page.wait_for_timeout(800)

    def _calendario_alvo():
        """Retorna o elemento .mx-calendar cujo cabeçalho == (mm, aaaa), ou None."""
        for cal in page.query_selector_all(".mx-calendar"):
            lbl = cal.query_selector(".mx-calendar-header-label")
            if not lbl:
                continue
            cmm, caaaa = _ler_mes_ano(lbl)
            if cmm == mm and caaaa == aaaa:
                return cal
        return None

    # navega: compara o 1º calendário visível com o alvo e clica prev/next.
    for _ in range(36):
        cal = _calendario_alvo()
        if cal:
            break
        labels = page.query_selector_all(".mx-calendar-header-label")
        if not labels:
            break
        cmm, caaaa = _ler_mes_ano(labels[0])
        if cmm is None:
            break
        alvo_ord, cur_ord = aaaa * 12 + mm, caaaa * 12 + cmm
        seta = ".mx-btn-icon-left" if alvo_ord < cur_ord else ".mx-btn-icon-right"
        btn = page.query_selector(seta)
        if not btn:
            break
        try:
            btn.click()
        except Exception:
            break
        page.wait_for_timeout(400)

    cal = _calendario_alvo()
    if not cal:
        return False
    for cell in cal.query_selector_all(".mx-table-date td"):
        try:
            cls = cell.get_attribute("class") or ""
            if any(x in cls for x in ("not-current-month", "inactive", "disabled")):
                continue
            if (cell.inner_text() or "").strip() == str(dd) and cell.is_visible():
                cell.click()
                page.wait_for_timeout(600)
                return True
        except Exception:
            continue
    return False


def _btn_por_texto(page, inclui, exclui=None):
    for b in page.query_selector_all("button, a"):
        t = (b.inner_text() or "").upper()
        if inclui in t and (not exclui or exclui not in t):
            return b
    return None


def _gerar_uma_vez(page, dia_str, destino) -> str:
    if not _selecionar_data_calendario(page, dia_str):
        return ""
    page.mouse.click(430, 107)  # fecha o calendário clicando no título
    page.wait_for_timeout(500)
    btn_gerar = _btn_por_texto(page, "GERAR", exclui="PDF")
    if not btn_gerar:
        return ""
    try:
        btn_gerar.click(force=True)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=25000)
    except Exception:
        pass
    # poll: espera o resumo ("Relatório de glosas") e o botão "...EM PDF" aparecerem
    btn_pdf = None
    for _ in range(20):
        corpo = re.sub(r"\s+", " ", (page.inner_text("body") or "")).lower()
        btn_pdf = _btn_por_texto(page, "PDF")
        if btn_pdf and "relatório de glosas" in corpo:
            break
        page.wait_for_timeout(700)
    if not btn_pdf:
        return ""
    try:
        with page.expect_download(timeout=25000) as di:
            btn_pdf.click()
        di.value.save_as(destino)
        return destino
    except Exception:
        return ""


def gerar_pdf_glosa(page, dia_str, destino) -> str:
    """No Relatório de Glosa, escolhe a data, gera e baixa o PDF detalhado.
    Faz até 2 tentativas (geração às vezes precisa de mais tempo de render).
    Retorna o caminho do PDF salvo (ou '' se não gerou)."""
    for _ in range(2):
        r = _gerar_uma_vez(page, dia_str, destino)
        if r:
            return r
        page.wait_for_timeout(1500)
    return ""


def checar_recurso(page, guia) -> str:
    """Abre Recurso de Glosa para uma guia e retorna o estado observado:
      RECURSAVEL   -> mostra opções de recurso (ABRIR GTO COMPLEMENTAR / RECURSAR)
      SEM_GLOSADO  -> 'não apresenta eventos glosados'
      INDEFINIDO   -> não foi possível ler."""
    fld = None
    for el in page.query_selector_all("input"):
        try:
            if el.is_visible():
                fld = el
                break
        except Exception:
            continue
    if not fld:
        return "INDEFINIDO"
    try:
        fld.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
    except Exception:
        pass
    fld.type(str(guia), delay=70)
    page.wait_for_timeout(300)
    for b in page.query_selector_all("button"):
        if "PROSSEGUIR" in (b.inner_text() or "").upper():
            try:
                b.click(force=True)
            except Exception:
                pass
            break
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(3500)
    corpo = re.sub(r"\s+", " ", (page.inner_text("body") or "")).lower()
    if "não apresenta eventos glosados" in corpo or "nao apresenta eventos glosados" in corpo:
        return "SEM_GLOSADO"
    if "recursar na guia" in corpo or "abrir gto complementar" in corpo:
        return "RECURSAVEL"
    return "INDEFINIDO"


# Glosa cuja própria existência indica que JÁ houve recurso (reanálise) — rejeitado.
GLOSAS_RECURSO_JA_FEITO = {"2908"}  # "Solicitação de reanálise efetuada de forma incorreta"


def _num_brl(s):
    """'1.234,56' -> 1234.56 ; '' -> None."""
    if not s:
        return None
    try:
        return float(s.replace(".", "").replace(",", "."))
    except Exception:
        return None


# ── Demonstrativo de Pagamento (resultado financeiro / recurso) ─────────────────
def _demo_set_guia(page, guia):
    """Preenche o campo (input height=0 do Vuetify) com o nº da guia e sincroniza."""
    inp = page.query_selector('input[type="text"]')
    if not inp:
        return False
    inp.evaluate("el=>el.focus()")
    page.wait_for_timeout(120)
    page.keyboard.type(str(guia), delay=60)
    inp.evaluate("""(el,v)=>{el.value=v;
        el.dispatchEvent(new Event('input',{bubbles:true}));
        el.dispatchEvent(new Event('change',{bubbles:true}));}""", str(guia))
    page.wait_for_timeout(300)
    return True


def consultar_demonstrativo(page, guia) -> dict:
    """Consulta o Demonstrativo de Pagamento de uma guia e devolve
    {tem_dados, bruto, glosado, pago}. Depois clica em NOVA BUSCA p/ a próxima.
    Deve ser chamado já na tela /demonstrativoPagamento (modo 'Número da guia')."""
    out = {"tem_dados": False, "bruto": None, "glosado": None, "pago": False}
    if not _demo_set_guia(page, guia):
        return out
    page.mouse.move(1100, 650)
    page.wait_for_timeout(150)
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
    # processado quando há valor bruto > 0; pago quando processado e há pagamento
    out["tem_dados"] = bool(out["bruto"] and out["bruto"] > 0)
    out["pago"] = bool(out["tem_dados"] and not sem_pg)
    # reseta p/ a próxima guia
    nb = _btn_por_texto(page, "NOVA BUSCA")
    if nb:
        try:
            nb.click(force=True)
        except Exception:
            pass
        page.wait_for_timeout(1200)
    return out


def classificar(glosa_cod, recurso_estado, demo=None) -> str:
    """Situação derivada (só do portal) para o panorama da empresária:
      RECURSO_REJEITADO   -> glosa 2908: reanálise (recurso) feita incorretamente
                             = JÁ passou pelo recurso e foi recusada (refazer).
      RESOLVIDA           -> Demonstrativo: pago e sem glosa (recurso deferido/pago).
      GLOSA_CONFIRMADA    -> Demonstrativo: glosa efetivada no pagamento (indeferida).
      A_RECORRER          -> glosada e recurso disponível na guia.
      RECURSO_OU_RESOLVIDA-> recursável, mas a guia já não mostra eventos glosados.
      NAO_RECURSAVEL      -> recuperação de valores / sem opção de recurso.
      GLOSADA             -> glosada (estado não verificado)."""
    if glosa_cod in GLOSAS_RECURSO_JA_FEITO:
        return "RECURSO_REJEITADO"
    if demo and demo.get("tem_dados"):
        if demo.get("pago") and (demo.get("glosado") or 0) == 0:
            return "RESOLVIDA"
        if (demo.get("glosado") or 0) > 0:
            return "GLOSA_CONFIRMADA"
    nao_recursavel = glosa_cod in GLOSAS_NAO_RECURSAVEIS
    if recurso_estado == "RECURSAVEL":
        return "A_RECORRER"
    if recurso_estado == "SEM_GLOSADO":
        return "NAO_RECURSAVEL" if nao_recursavel else "RECURSO_OU_RESOLVIDA"
    return "NAO_RECURSAVEL" if nao_recursavel else "GLOSADA"


def extrair_unidade(pw, conta, label, dia_str, destino_dir, checar_recursos=True,
                    checar_demonstrativo=True, log=print) -> dict:
    """Extrai glosas de uma unidade: gera/parseia o PDF, checa o estado de recurso
    de cada guia distinta e (opcional) cruza com o Demonstrativo de Pagamento
    (resultado financeiro/recurso). Retorna {conta,label,periodo,eventos}."""
    user, pwd = conta, get_credentials_odonto()[1]
    os.makedirs(destino_dir, exist_ok=True)
    pdf_path = os.path.join(destino_dir, f"glosa_{conta}.pdf")

    b, c, page = login_odonto(pw, user, pwd)
    eventos = []
    try:
        if not _abrir_topo(page, "Recurso de Glosa") or not _clicar_subitem(page, "RELAT"):
            raise RuntimeError("não cheguei no Relatório de Glosa")
        salvo = gerar_pdf_glosa(page, dia_str, pdf_path)
        if not salvo:
            raise RuntimeError("não consegui gerar/baixar o PDF de glosa")
        eventos = parse_glosa_pdf(salvo)
        log(f"[{label}] {len(eventos)} evento(s) de glosa no PDF (período até {dia_str})")
    finally:
        try:
            b.close()
        except Exception:
            pass

    estados = {}
    if checar_recursos and eventos:
        guias = sorted({e["ficha"] for e in eventos})
        log(f"[{label}] checando recurso de {len(guias)} guia(s) distinta(s)...")
        b, c, page = login_odonto(pw, user, pwd)
        try:
            _abrir_topo(page, "Recurso de Glosa")
            _clicar_subitem(page, "RECURSO DE GLOSA")
            for i, g in enumerate(guias, 1):
                try:
                    estados[g] = checar_recurso(page, g)
                except Exception:
                    estados[g] = "INDEFINIDO"
                if i % 10 == 0 or i == len(guias):
                    log(f"[{label}]   recurso {i}/{len(guias)}")
                # volta para a tela de consulta para a próxima guia
                try:
                    _abrir_topo(page, "Recurso de Glosa")
                    _clicar_subitem(page, "RECURSO DE GLOSA")
                except Exception:
                    pass
        finally:
            try:
                b.close()
            except Exception:
                pass

    demos = {}
    if checar_demonstrativo and eventos:
        guias = sorted({e["ficha"] for e in eventos})
        log(f"[{label}] consultando demonstrativo de {len(guias)} guia(s)...")
        b, c, page = login_odonto(pw, user, pwd)
        try:
            _abrir_topo(page, "Financeiro")
            _clicar_subitem(page, "DEMONSTRATIVO")
            page.wait_for_timeout(1200)
            page.mouse.move(1100, 400)
            page.mouse.click(1100, 400)  # fecha o flyout do menu lateral
            page.wait_for_timeout(500)
            for i, g in enumerate(guias, 1):
                try:
                    demos[g] = consultar_demonstrativo(page, g)
                except Exception:
                    demos[g] = None
                if i % 10 == 0 or i == len(guias):
                    log(f"[{label}]   demonstrativo {i}/{len(guias)}")
        finally:
            try:
                b.close()
            except Exception:
                pass

    for e in eventos:
        e["conta"] = conta
        e["unidade"] = label
        e["recurso_estado"] = estados.get(e["ficha"], "NAO_CHECADO")
        demo = demos.get(e["ficha"])
        e["demo_glosado"] = demo.get("glosado") if demo else None
        e["demo_pago"] = bool(demo.get("pago")) if demo else False
        e["situacao"] = classificar(e["glosa_cod"], e["recurso_estado"], demo)
    return {"conta": conta, "label": label, "dia": dia_str, "eventos": eventos}
