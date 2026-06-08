"""
Integração com o Portal do Credenciado OdontoPrev (RedeUna).
Encapsula: login, consulta de GTOs por período, abertura de GTO e upload
de arquivos (imagens + laudos). 100% determinístico (Playwright).

Fluxo mapeado e validado em 08/06/2026 (GTO 193176575 - CHIMENE):
  login -> Atendimento -> Consultar GTOs -> Período (DD/MM/AAAA - DD/MM/AAAA)
  -> CONSULTAR -> tabela de GTOs -> ABRIR GTO (popup /portal/guia/<n>)
  -> seção "Anexar documentação": input[type=file] OCULTO (multiple) -> upload imediato.
"""

import os
import re
import unicodedata

BASE = "https://credenciado.odontoprev.com.br"
LOGIN_URL = f"{BASE}/"


def get_credentials_odonto():
    user = os.environ.get("ODONTOPREV_USER", "***REMOVED***")
    pwd = os.environ.get("ODONTOPREV_PASSWORD", "***REMOVED***")
    return user, pwd


def normaliza_nome(nome: str) -> str:
    """MAIÚSCULAS, sem acento, espaços colapsados — chave de comparação."""
    s = unicodedata.normalize("NFKD", nome or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s


# ── Login ──────────────────────────────────────────────────────────────────────
def login_odonto(pw, user: str, password: str):
    """Retorna (browser, ctx, page) logado no portal. Lança RuntimeError se falhar."""
    browser = pw.chromium.launch(
        headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    ctx = browser.new_context(viewport={"width": 1500, "height": 900})
    page = ctx.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    page.fill('input[name="username"]', user)
    page.fill('input[name="current-password"]', password)
    page.click('button[type="submit"]')
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(4000)
    if page.query_selector('input[name="current-password"]'):
        browser.close()
        raise RuntimeError("Falha no login OdontoPrev (campo de senha ainda visível).")
    return browser, ctx, page


# ── Navegação até Consultar GTOs ────────────────────────────────────────────────
def abrir_consultar_gtos(page):
    """Abre Atendimento (mouse real, pois clique é interceptado) -> Consultar GTOs."""
    at = None
    for _ in range(20):
        for el in page.query_selector_all(".c-menu-itens.c-subMenu"):
            if (el.inner_text() or "").strip().startswith("Atendimento"):
                at = el
                break
        if at:
            break
        page.wait_for_timeout(1000)
    if not at:
        raise RuntimeError("Menu 'Atendimento' não apareceu.")
    box = at.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(700)
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(1800)
    for el in page.query_selector_all("li.a-item-submenu"):
        if "CONSULTAR GTO" in (el.inner_text() or "").upper():
            el.click()
            break
    else:
        raise RuntimeError("Item 'Consultar GTOs' não encontrado no submenu.")
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(3000)


def consultar_periodo(page, dia: str):
    """Seleciona filtro Período = dia único (DD/MM/AAAA - DD/MM/AAAA) e consulta.
    dia: 'DD/MM/YYYY'. Deve ser chamado já em /portal/consultaGTOs."""
    # fecha o flyout do submenu clicando em área neutra
    page.mouse.click(1100, 600)
    page.wait_for_timeout(600)
    page.click("text=Período", timeout=8000)
    page.wait_for_timeout(900)
    fld = page.query_selector("#input-324") or page.query_selector(
        "input[type=text]:below(:text('Selecione o período'))"
    )
    fld.click()
    page.wait_for_timeout(300)
    fld.type(f"{dia} - {dia}", delay=50)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    page.click("button:has-text('CONSULTAR')")
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(5000)


# ── Parse da tabela de GTOs ──────────────────────────────────────────────────────
# Colunas: Número da GTO | Liberação da senha | Nome | Validade | Status | Dias
_RE_LINHA = re.compile(
    r"^(\d{6,})\s+(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(\d+\s*dias?|.*)$"
)


def listar_gtos(page) -> list:
    """Lê a tabela de resultados e retorna lista de dicts por GTO."""
    gtos = []
    for tr in page.query_selector_all("tr, [role=row]"):
        txt = re.sub(r"\s+", " ", (tr.inner_text() or "")).strip()
        m = re.match(r"^(\d{6,})\s+(\d{2}/\d{2}/\d{4})\s+(.+)$", txt)
        if not m:
            continue
        gto = m.group(1)
        liberacao = m.group(2)
        resto = m.group(3)
        # validade = próxima data; o que vem antes dela é o nome
        mv = re.search(r"(\d{2}/\d{2}/\d{4})", resto)
        nome = resto[: mv.start()].strip() if mv else resto
        depois = resto[mv.end():].strip() if mv else ""
        validade = mv.group(1) if mv else ""
        # status = depois, removendo 'ABRIR GTO' e 'N dias'
        status = re.sub(r"ABRIR GTO", "", depois, flags=re.I)
        status = re.sub(r"\d+\s*dias?", "", status).strip(" |").strip()
        gtos.append({
            "gto": gto, "liberacao": liberacao, "nome": nome,
            "nome_norm": normaliza_nome(nome), "validade": validade,
            "status": status, "raw": txt,
        })
    return gtos


def _anexos_count(page) -> int:
    body = re.sub(r"\s+", " ", page.inner_text("body"))
    m = re.search(r"total de anexos\)\s*:\s*(\d+)", body, re.I)
    return int(m.group(1)) if m else -1


def abrir_gto(page, gto: str):
    """Clica em 'ABRIR GTO' da linha cujo número == gto. Retorna a popup page."""
    alvo = None
    for tr in page.query_selector_all("tr, [role=row]"):
        if gto in (tr.inner_text() or ""):
            alvo = tr.query_selector("button:has-text('ABRIR'), a:has-text('ABRIR')")
            if alvo:
                break
    if not alvo:
        raise RuntimeError(f"Linha da GTO {gto} não encontrada.")
    with page.expect_popup() as pop:
        alvo.click()
    gp = pop.value
    gp.wait_for_load_state("domcontentloaded")
    gp.wait_for_timeout(5000)
    return gp


def ler_dados_gto(gp) -> dict:
    """Extrai carteirinha, nome e nº de anexos da página de detalhe da GTO."""
    body = re.sub(r"\s+", " ", gp.inner_text("body"))
    cart = re.search(r"Carteirinha\s*([0-9]{6,})", body, re.I)
    nome = re.search(r"Nome\s+([A-ZÀ-Ú][A-ZÀ-Úa-zà-ú ]+?)\s+Carteirinha", body)
    return {
        "url": gp.url,
        "carteirinha": cart.group(1) if cart else "",
        "nome": (nome.group(1).strip() if nome else ""),
        "anexos": _anexos_count(gp),
    }


def upload_arquivos(gp, arquivos: list) -> dict:
    """Anexa a lista de arquivos na GTO aberta (input[type=file] oculto, multiple).
    O portal envia imediatamente. Retorna {anexos_antes, anexos_depois, ok}."""
    antes = _anexos_count(gp)
    # achar o botão UPLOAD (alguns layouts disparam o input ao clicar)
    fi = gp.query_selector("input[type=file]")
    if not fi:
        for el in gp.query_selector_all("button, .v-btn, a"):
            if (el.inner_text() or "").strip().upper() == "UPLOAD":
                try:
                    el.click()
                except Exception:
                    pass
                break
        gp.wait_for_timeout(1000)
        fi = gp.query_selector("input[type=file]")
    if not fi:
        raise RuntimeError("input[type=file] de upload não encontrado na GTO.")
    fi.set_input_files(arquivos)
    gp.wait_for_timeout(2000 + 1500 * len(arquivos))
    depois = _anexos_count(gp)
    body = re.sub(r"\s+", " ", gp.inner_text("body")).lower()
    return {
        "anexos_antes": antes,
        "anexos_depois": depois,
        "ok": depois >= antes + len(arquivos) if antes >= 0 else "sucesso" in body,
    }
