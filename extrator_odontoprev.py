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
from urllib.parse import urlparse, unquote

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


# ── Proxy (SOMENTE OdontoPrev) ───────────────────────────────────────────────
# O servidor (datacenter) é bloqueado pelo OdontoPrev (rate-limit/anti-bot). Um
# proxy residencial STICKY (IP fixo durante a sessão) contorna. O PRORADIS é
# acessado DIRETO (sem proxy) — por isso a var é ODONTO_PROXY_URL, não global.
# Formato esperado: http://usuario:senha@host:porta
def _odo_proxy_url() -> str:
    return (os.environ.get("ODONTO_PROXY_URL") or "").strip()


def _fresh_sessid(username: str) -> str:
    """Se o proxy usa ';sessid.<x>' (sticky DataImpulse), troca <x> por um token
    NOVO a cada chamada -> um IP residencial BR diferente por sessao, porém ESTAVEL
    durante ela. Espalha a carga entre varios IPs e evita re-bloqueio no mesmo IP."""
    import binascii
    if ";sessid." not in (username or ""):
        return username
    tok = binascii.hexlify(os.urandom(4)).decode()
    return re.sub(r";sessid\.[^;]*", f";sessid.{tok}", username)


def _odo_playwright_proxy():
    """Dict de proxy pro Playwright (chromium.launch). None se não configurado.
    Cada chamada gera um sessid novo (IP BR fresco, sticky durante a sessao)."""
    url = _odo_proxy_url()
    if not url:
        return None
    p = urlparse(url)
    server = f"{p.scheme or 'http'}://{p.hostname}"
    if p.port:
        server += f":{p.port}"
    proxy = {"server": server}
    if p.username:
        proxy["username"] = _fresh_sessid(unquote(p.username))
    if p.password:
        proxy["password"] = unquote(p.password)
    return proxy


def _odo_requests_proxies():
    """Dict de proxies pro requests (http/https). None se não configurado.
    Reconstroi a URL com sessid novo (mesmo IP BR pra todos os requests da sessao)."""
    url = _odo_proxy_url()
    if not url:
        return None
    p = urlparse(url)
    user = _fresh_sessid(unquote(p.username or ""))
    pwd = unquote(p.password or "")
    hostport = (p.hostname or "") + (f":{p.port}" if p.port else "")
    auth = f"{user}:{pwd}@" if user else ""
    base = f"{p.scheme or 'http'}://{auth}{hostport}"
    return {"http": base, "https": base}


def normaliza_nome(nome: str) -> str:
    """MAIÚSCULAS, sem acento, espaços colapsados — chave de comparação."""
    s = unicodedata.normalize("NFKD", nome or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s


# ── Login ──────────────────────────────────────────────────────────────────────
def login_odonto(pw, user: str, password: str):
    """Retorna (browser, ctx, page) logado no portal. Lança RuntimeError se falhar."""
    for tentativa in range(3):
        _pxy = _odo_playwright_proxy()   # sessid novo por tentativa -> IP BR fresco
        if _pxy:
            print(f"[_odonto] login via proxy {_pxy['server']} (tentativa {tentativa+1}/3)", flush=True)
        browser = pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"],
            proxy=_pxy,
        )
        ctx = browser.new_context(
            viewport={"width": 1500, "height": 900},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    
        # SPA Vue/Vuetify pode hidratar devagar (sobretudo via proxy residencial).
        # Espera os campos ficarem VISIVEIS + rede ociosa ANTES de preencher; senao o
        # v-model do Vue nao captura o valor e o submit acusa "Campo obrigatorio".
        try:
            page.wait_for_selector('input[name="username"]', state="visible", timeout=45000)
            page.wait_for_selector('input[name="current-password"]', state="visible", timeout=45000)
        except Exception:
            browser.close(); continue
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(1200)

        def _fill_robusto(sel, val):
            loc = page.locator(sel)
            for _ in range(3):
                try:
                    loc.click(timeout=8000); loc.fill(val)
                except Exception:
                    pass
                try:
                    if loc.input_value() == val:
                        page.keyboard.press("Tab"); return
                except Exception:
                    pass
                # fallback: seta o valor via JS e dispara os eventos que o Vue escuta
                try:
                    page.eval_on_selector(
                        sel,
                        "(el, v) => { el.value = v;"
                        " el.dispatchEvent(new Event('input', {bubbles:true}));"
                        " el.dispatchEvent(new Event('change', {bubbles:true})); }",
                        val)
                except Exception:
                    pass
            page.keyboard.press("Tab")

        _fill_robusto('input[name="username"]', user)
        _fill_robusto('input[name="current-password"]', password)

        # confirma que AMBOS ficaram preenchidos ANTES de enviar
        try:
            u_ok = page.locator('input[name="username"]').input_value() == user
            p_ok = page.locator('input[name="current-password"]').input_value() == password
        except Exception:
            u_ok = p_ok = False
        if not (u_ok and p_ok):
            print(f"[_odonto] campos nao preencheram (tentativa {tentativa+1}/3), retry", flush=True)
            browser.close(); continue

        page.click('button[type="submit"]')
        
        try:
            page.wait_for_selector('input[name="current-password"]', state="hidden", timeout=40000)
        except Exception:
            pass
        
        if not page.query_selector('input[name="current-password"]') or not page.is_visible('input[name="current-password"]'):
            return browser, ctx, page
            
        bloqueado = page.query_selector("text=/Aguarde.*segundos/i") or page.query_selector("text=/Usuário bloqueado/i")
        if bloqueado:
            print(f"[_odonto_setup] Rate-limit detectado. Fechando browser. Aguardando 85s para tentar novamente (tentativa {tentativa+1}/3)...", flush=True)
            browser.close()
            import time
            time.sleep(85)
        else:
            print(f"[_odonto] login nao completou (tentativa {tentativa+1}/3), retry", flush=True)
            try:
                page.screenshot(path="login_failed.png")
            except Exception:
                pass
            browser.close()
    raise RuntimeError("Falha no login OdontoPrev apos 3 tentativas (campo de senha ainda visivel).")


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
    datas_vistas = set()
    for _ in range(timeout * 2):
        n_dia = 0
        datas_vistas = set()
        for tr in page.query_selector_all("tr, [role=row]"):
            t = re.sub(r"\s+", " ", (tr.inner_text() or "")).strip()
            m = re.match(r"^(\d{6,})\s+(\d{2}/\d{2}/\d{4})", t)
            if m:
                datas_vistas.add(m.group(2))
                if m.group(2) == alvo:
                    n_dia += 1
        if n_dia:
            return n_dia
        body = (page.inner_text("body") or "").lower()
        if "nenhum" in body and any(k in body for k in
                                    ("registro", "dado", "resultado", "encontrad")):
            return 0
        page.wait_for_timeout(500)
    # Esgotou o tempo. Se HÁ linhas de GTO mas nenhuma casou com o dia, quase sempre
    # é divergência de formato de data (locale) — registra o que foi visto p/ diag.
    if datas_vistas:
        print(f"[_aguardar_tabela_gtos] 0 linhas para {alvo}, mas vi datas: "
              f"{sorted(datas_vistas)[:8]} (possível locale/formato de data)")
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


def _anexos_count(page, tentativas: int = 6) -> int:
    """Quantos anexos a guia ja tem. -1 se nao conseguiu ler.

    LE COM RETRY. A secao de anexos e renderizada pelo Vue e as vezes ainda nao
    esta no DOM na primeira leitura. Uma leitura unica devolvia -1, e a trava de
    duplicidade (correta) recusava o envio: TAMIRES DE SOUZA, ANDRE LUIS SILVA
    CONCEICAO e MARCOS FERREIRA CAMPOS (23/07) tinham documentacao OK e ficaram
    sem faturar por causa disso. Insistir e barato; recusar por engano nao e."""
    for i in range(max(1, tentativas)):
        try:
            body = re.sub(r"\s+", " ", page.inner_text("body"))
            m = re.search(r"total de anexos\)\s*:\s*(\d+)", body, re.I)
            if m:
                return int(m.group(1))
        except Exception:
            pass
        if i < tentativas - 1:
            try:
                page.wait_for_timeout(700)
            except Exception:
                break
    return -1


def _chave_anexo(nome: str) -> tuple:
    """Identidade de um anexo, independente de variacoes do nome.

      LAUDO_<exame>_<acc>_<TIPO>.pdf -> ("laudo", acc, TIPO)   (ignora o rotulo do
          exame, que ja mudou uma vez e faria o mesmo laudo subir de novo)
      ENTREGA_<hash>.jpg             -> ("img", hash)          (nome vem do conteudo)
      ENTREGA_<numero>.jpg           -> ("img_legado", numero) (anexos antigos)
      SOLICITACAO_*                  -> ("solic", nome)        (nome ja e estavel)
    """
    b = os.path.basename((nome or "").strip())
    u = b.upper()
    m = re.match(r"LAUDO_.+?_(\d+)_([A-Z]+)", u)
    if m:
        return ("laudo", m.group(1), m.group(2))
    m = re.match(r"ENTREGA_([0-9A-F]{6,})\.", u)
    if m:
        return ("img", m.group(1).lower())
    m = re.match(r"ENTREGA_(\d+)\.", u)
    if m:
        return ("img_legado", m.group(1))
    if u.startswith("SOLICITACAO_"):
        return ("solic", u)
    return ("outro", u)


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
            # Espera inteligente (em vez de 5s fixos): retorna assim que a popup
            # do detalhe está pronta — marcadores do GTO ou o input de upload.
            # Teto ~5s; em popup rápida economiza vários segundos por GTO.
            for _ in range(20):
                try:
                    body = gp.inner_text("body")
                    if ("Carteirinha" in body or "anexos" in body.lower()
                            or gp.query_selector("input[type=file]")):
                        break
                except Exception:
                    pass
                gp.wait_for_timeout(250)
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


def _resolver_contagem(dom_n, dom_nomes, fallback):
    """Contagem+nomes de anexos com o DOM como fonte PRIMARIA e um fallback
    AUTORITATIVO (a API /v1/gto/imagens, INJETADA pela esteira) SO quando o DOM
    falhou (dom_n < 0). fallback() deve devolver (count, nomes). Retorna (n, nomes).

    Existe porque o scrape do DOM (regex 'total de anexos)') falha em algumas guias
    RedeUna de forma persistente — casos JOSE/LEONARDO/SUELEM/RAFAEL/MARIA SOPHIA
    (28/07): documentacao OK e mesmo assim -1, presas no ponto de escrita. A API e a
    MESMA lista que a descoberta ja confia (nomeArquivo + imagemGTO), entao count e
    idempotencia por nome seguem valendo; NAO sub-conta.

    GUARDRAIL: se o DOM falhou E o fallback tambem (ou e ausente/estoura), devolve o
    proprio dom_n (< 0) — e as travas de upload_arquivos bloqueiam. NUNCA devolve uma
    contagem 'chutada': anexar em duplicidade e irreversivel."""
    if isinstance(dom_n, int) and dom_n >= 0:
        return dom_n, dom_nomes
    if callable(fallback):
        try:
            api_n, api_nomes = fallback()
        except Exception:
            api_n, api_nomes = -1, set()
        if isinstance(api_n, int) and api_n >= 0:
            return api_n, (api_nomes if isinstance(api_nomes, set) else set())
    return dom_n, dom_nomes


def upload_arquivos(gp, arquivos: list, max_antes: int = 1, contar_fallback=None) -> dict:
    """Anexa a lista de arquivos na GTO aberta (input[type=file] oculto, multiple).
    O portal envia imediatamente.

    IDEMPOTENTE: arquivos cujo nome-base já está anexado são pulados (permite
    reprocessar o dia sem duplicar). VERIFICAÇÃO ROBUSTA: confirma por NOME de
    arquivo (com polling), não pela aritmética do contador — que atualiza tarde
    na popup e gerava falso 'ERRO_UPLOAD'.

    max_antes = maior nº de anexos pré-existentes que ainda permite enviar.
    Default 1 (a própria GTO). A esteira passa o nº de CÓPIAS ASSINADAS da GTO
    contado na descoberta (flag imagemGTO da API) — guia RE-ASSINADA tem 2+
    cópias da GTO sem documentação nenhuma e precisa receber anexo (casos
    PAULO SERGIO/WELLINGHTON/FABIO/PATRICK, 27/07).

    contar_fallback (opcional): callable () -> (count, nomes) com a fonte
    AUTORITATIVA da API, usada SO se o scrape do DOM falhar. Sem ele (caminhos que
    nao injetam), o comportamento e exatamente o de antes.
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
    # DOM falhou (guia cujo 'total de anexos)' nao renderiza)? Usa a fonte
    # autoritativa da API para count E nomes. Se a API tambem falhar, antes fica
    # < 0 e as travas abaixo bloqueiam — nada e enviado.
    antes, nomes_antes = _resolver_contagem(antes, nomes_antes, contar_fallback)

    # "Nada para enviar" NÃO é "tudo anexado". Uma lista vazia caía no mesmo
    # `return ok=True` da idempotência, o chamador lia sucesso e registrava a guia
    # como FATURADA sem ter subido arquivo nenhum (caso JOEL, 20/07: a filtragem
    # de exame particular esvaziou a lista e a GTO foi dada como resolvida).
    if not arquivos:
        return {"anexos_antes": antes, "anexos_depois": antes, "ja_anexados": [],
                "enviados": [], "ok": False,
                "erro": "nenhum arquivo para anexar"}

    # ── TRAVA DE DUPLICIDADE (regra do dono, 29/07) ──────────────────────────
    # O OdontoPrev NAO PERMITE REMOVER anexo. Cada arquivo repetido e dano
    # PERMANENTE na guia — casos CLAUDIA REGINA e VANESSA SILVA BATISTA, que
    # chegaram a 12 anexos com imagens visivelmente duplicadas.
    #
    # Regra: toda guia nasce com a(s) copia(s) assinada(s) da propria GTO —
    # normalmente 1; uma RE-ASSINATURA pode criar mais. `max_antes` e o nº de
    # anexos comprovadamente copia da GTO (a esteira conta pelo flag imagemGTO
    # da API, na descoberta). Acima disso, alguem ja anexou (nos ou um humano)
    # ou algo mudou desde a descoberta — e NAO ha o que acrescentar.
    #
    # Esta guarda fica AQUI, e nao em quem chama, porque upload_arquivos e o UNICO
    # ponto de escrita do sistema: os tres caminhos que anexam (esteira.py:1602,
    # fechar_dia.py:478, ciclo_completo.py:185) passam por ela. Protegido aqui,
    # esta protegido em todos — inclusive em caminho novo que alguem escreva depois.
    try:
        max_antes = max(1, int(max_antes))
    except Exception:
        max_antes = 1
    if antes is None or antes < 0:
        return {"anexos_antes": antes, "anexos_depois": antes, "ja_anexados": [],
                "enviados": [], "ok": False,
                "erro": ("nao foi possivel LER quantos anexos a guia ja tem — "
                         "nada enviado, para nao arriscar duplicar (o portal nao "
                         "permite remover anexo)")}
    if antes > max_antes:
        return {"anexos_antes": antes, "anexos_depois": antes,
                "ja_anexados": sorted(nomes_antes), "enviados": [], "ok": False,
                "erro": (f"a guia ja tem {antes} anexos — nada enviado (esperado "
                         f"ate {max_antes} copia(s) da propria GTO). Acima disso "
                         f"a documentacao ja foi anexada ou algo mudou desde a "
                         f"descoberta. O portal nao permite remover anexo, entao "
                         f"duplicar seria irreversivel")}

    # IDEMPOTENCIA POR IDENTIDADE, nao por nome exato. Comparar o nome inteiro era
    # fragil: "ENTREGA_1.jpg" tem numero POSICIONAL (muda se a ordem de captura
    # mudar) e "LAUDO_<exame>_..." carrega um rotulo que ja mudou (LAUDO_ATM_ ->
    # LAUDO_INTERPROXIMAL_ apos a correcao do rotulo). Qualquer um dos dois fazia a
    # MESMA coisa ser anexada de novo — casos CLAUDIA REGINA e VANESSA SILVA
    # BATISTA, que chegaram a 12 anexos com imagens repetidas.
    # Requisito do dono: rodar o mesmo dia 300 vezes deve anexar UMA vez.
    chaves_portal = {_chave_anexo(n) for n in nomes_antes}
    _tem_img_legado = any(k[0] == "img_legado" for k in chaves_portal)
    por_base, ja, faltam = {}, [], []
    for a in arquivos:
        b = os.path.basename(a)
        if b in por_base:
            continue
        por_base[b] = a
        k = _chave_anexo(b)
        # Imagem legada no portal ("ENTREGA_<numero>"): o nome nao diz o conteudo,
        # entao nao da para saber se a nossa ja esta la. Nao reenvia — repetir e
        # pior do que faltar, e a guia ja tem imagem.
        if k in chaves_portal or (k[0] == "img" and _tem_img_legado):
            ja.append(b)
        else:
            faltam.append(a)
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
