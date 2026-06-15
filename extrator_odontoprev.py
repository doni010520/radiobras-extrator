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

# Carrega .env em desenvolvimento local (no Render/EasyPanel as vars já vêm do ambiente).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE = "https://credenciado.odontoprev.com.br"
LOGIN_URL = f"{BASE}/"


def get_credentials_odonto():
    user = os.environ.get("ODONTOPREV_USER")
    pwd = os.environ.get("ODONTOPREV_PASSWORD")
    if not user or not pwd:
        raise RuntimeError(
            "Credenciais do OdontoPrev ausentes. Defina ODONTOPREV_USER e "
            "ODONTOPREV_PASSWORD nas variáveis de ambiente (ou no arquivo .env)."
        )
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
    page.wait_for_timeout(1200)
    # O id do campo é dinâmico (Vuetify) — localizar pelo label, com fallbacks.
    fld = None
    try:
        fld = page.query_selector(
            "xpath=//*[contains(normalize-space(.),'Selecione o per')]"
            "/ancestor-or-self::div[contains(@class,'v-input')][1]//input"
        )
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
    if not fld:
        raise RuntimeError("Campo de período não encontrado na tela Consultar GTOs.")
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
    # Espera a tabela popular com GTOs do dia pedido (em vez de tempo fixo) —
    # corrige o "0 GTOs" por leitura antes da tabela carregar em servidor lento.
    n = _aguardar_tabela_gtos(page, dia)
    return n


def _aguardar_tabela_gtos(page, dia: str, timeout: int = 30) -> int:
    """Aguarda a tabela mostrar linhas de GTO cuja LIBERAÇÃO == `dia`, ou um
    indicador de 'nenhum registro'. Evita ler cedo demais (resultado 0 falso) e
    confirma que o FILTRO da data foi aplicado. Retorna nº de linhas do dia."""
    alvo = (dia or "").strip()
    for _ in range(timeout * 2):
        n_dia = 0
        for tr in page.query_selector_all("tr, [role=row]"):
            t = re.sub(r"\s+", " ", (tr.inner_text() or "")).strip()
            m = re.match(r"^(\d{6,})\s+(\d{2}/\d{2}/\d{4})", t)
            if m and m.group(2) == alvo:
                n_dia += 1
        if n_dia:
            return n_dia
        body = (page.inner_text("body") or "").lower()
        if "nenhum" in body and any(k in body for k in
                                    ("registro", "dado", "resultado", "encontrad")):
            return 0
        page.wait_for_timeout(500)
    return 0


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


def _anexos_nomes(page) -> set:
    """Conjunto de nomes de arquivo (.pdf/.jpg/.png) atualmente anexados na GTO.
    Usado para verificação por NOME (robusta) e idempotência (não reanexar)."""
    nomes = set()
    for el in page.query_selector_all("a, td, span, li, div"):
        try:
            t = (el.inner_text() or "").strip()
        except Exception:
            continue
        if re.search(r"\.(pdf|jpe?g|png)\b", t, re.I) and len(t) < 200:
            nomes.add(re.sub(r"\s+", " ", t).strip())
    return nomes


def _localizar_botao_abrir(page, gto: str):
    """Re-localiza (sempre fresco) o botão 'ABRIR' da linha da GTO. Handles do
    Vuetify viram stale entre iterações, então nunca reutilizar handle antigo."""
    for tr in page.query_selector_all("tr, [role=row]"):
        try:
            if gto in (tr.inner_text() or ""):
                b = tr.query_selector("button:has-text('ABRIR'), a:has-text('ABRIR')")
                if b:
                    return b
        except Exception:
            continue
    return None


def abrir_gto(page, gto: str, _refrescar=None):
    """Clica em 'ABRIR GTO' da linha cujo número == gto. Retorna a popup page.

    Resiliente (corrige cascata de 'ElementHandle.click timeout'): a cada
    tentativa dispensa overlays/modais, re-localiza a linha do zero, dá scroll e
    abre a popup com timeout curto. Se falhar e houver `_refrescar`, recarrega a
    lista e tenta de novo (até 3x)."""
    last = None
    for _ in range(3):
        # dispensar overlay/modal que possa cobrir o botão (causa do timeout)
        try:
            page.keyboard.press("Escape")
            page.mouse.click(1100, 600)
            page.wait_for_timeout(400)
        except Exception:
            pass
        alvo = _localizar_botao_abrir(page, gto)
        if not alvo:
            last = RuntimeError(f"Linha da GTO {gto} não encontrada.")
            if _refrescar:
                try:
                    _refrescar()
                except Exception:
                    pass
            continue
        try:
            alvo.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass
        try:
            with page.expect_popup(timeout=20000) as pop:
                alvo.click(timeout=10000)
            gp = pop.value
            gp.wait_for_load_state("domcontentloaded")
            gp.wait_for_timeout(5000)
            return gp
        except Exception as e:
            last = e
            if _refrescar:
                try:
                    _refrescar()
                except Exception:
                    pass
    raise last or RuntimeError(f"Falha ao abrir GTO {gto}")


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
    O portal envia imediatamente.

    IDEMPOTENTE: arquivos cujo nome-base já está anexado são pulados (permite
    reprocessar o dia sem duplicar). VERIFICAÇÃO ROBUSTA: confirma por NOME de
    arquivo (com polling), não pela aritmética do contador — que atualiza tarde
    na popup e gerava falso 'ERRO_UPLOAD'.
    Retorna {anexos_antes, anexos_depois, ja_anexados, enviados, ok}."""
    # Esperar a seção de anexos RENDERIZAR antes de ler nomes/contador — senão a
    # leitura antecipada faz a idempotência achar que faltam arquivos e procurar
    # um input que ainda não existe (falso 'input não encontrado').
    for _ in range(15):
        if _anexos_count(gp) >= 0 or gp.query_selector("input[type=file]"):
            break
        gp.wait_for_timeout(1000)
    antes = _anexos_count(gp)
    nomes_antes = _anexos_nomes(gp)

    # Idempotência: só enviar os que ainda não estão anexados (por nome-base).
    por_base = {}
    for a in arquivos:
        por_base.setdefault(os.path.basename(a), a)
    ja = [b for b in por_base if b in nomes_antes]
    faltam = [a for b, a in por_base.items() if b not in nomes_antes]
    if not faltam:
        return {"anexos_antes": antes, "anexos_depois": antes,
                "ja_anexados": ja, "enviados": [], "ok": True}

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
    fi.set_input_files(faltam)

    # Verificação por NOME com polling (até ~30s). O contador da popup demora a
    # refletir; os nomes dos novos anexos aparecem no DOM quando concluído.
    alvo_bn = {os.path.basename(a) for a in faltam}
    gp.wait_for_timeout(2000 + 1000 * len(faltam))
    ok = False
    for _ in range(30):
        nomes = _anexos_nomes(gp)
        if alvo_bn <= nomes:
            ok = True
            break
        body = re.sub(r"\s+", " ", gp.inner_text("body")).lower()
        if "sucesso" in body and _anexos_count(gp) >= antes + len(faltam):
            ok = True
            break
        gp.wait_for_timeout(1000)

    depois = _anexos_count(gp)
    return {
        "anexos_antes": antes,
        "anexos_depois": depois,
        "ja_anexados": ja,
        "enviados": sorted(alvo_bn),
        "ok": ok,
    }
