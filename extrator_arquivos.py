"""
extrator_arquivos.py — Download de imagens e laudos por paciente (RADIOBRAS SmartRIS)
Estratégia: Playwright em lotes (BATCH_SIZE pacientes por sessao Playwright).
Porta a logica de _entrega.py e _laudos_neuza.py — sem valores chumbados.
"""

import hashlib
import io
import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime

import cv2
import numpy as np
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from extrator_pacientes_analitico import (
    BASE_URL as BASE,
    get_credentials,
    parse_html_to_df,
    post_relatorio,
    resolve_tokens,
)

# ── Constantes ────────────────────────────────────────────────────────────────
BATCH_SIZE = 10
POPUP_WAIT_MS = 10000
MAX_RETRIES = 2
FORCE = False
# Tempo de espera pela abertura do popup reports_doc (event 'page'). O viewer às
# vezes demora >30s (default) -> timeouts esporádicos. 60s + retry reduz falhas.
EXPECT_PAGE_MS = 60000
POPUP_OPEN_RETRIES = 2

# Criterio de imagem entregavel: presenca da LOGO RadioBras Digital (VERDE) no canto
# superior-esquerdo da lamina. Deteccao 100% deterministica por COR, SEM OCR/IA.
# As radiografias cruas e os renders sao tons de cinza puro (R=G=B) -> fracao de verde
# = 0; as laminas de entrega tem a logo verde no cabecalho. Calibrado em amostras reais
# (entregaveis >= 0.0024, cruas <= 0.00001 -> gap de ~240x, corte em 0.0005).
LOGO_GREEN_MIN_FRAC = 0.0005
# Dedup perceptual (aHash 8x8): o viewer pode re-codificar a MESMA lamina (md5 difere),
# entao deduplicamos por similaridade perceptual. Duplicatas dao Hamming=0; laminas
# distintas (mesmo paciente) diferem por >5 bits -> tolerancia 4 e segura.
PHASH_DUP_MAX_HAMMING = 4

EXAME_KEYWORDS = [
    "PANORAMICA", "TELERRADIOGRAFIA", "DOCUMENTACAO", "CEFALOMETR",
    "PERIAPICAL", "FOTOGRAFIA", "SEIOS", "ATM", "MAO", "CARPAL",
]

_BAD_RE = re.compile(
    r"DOCUMENTA|PANORAM|TELERR|LAUDAR|COMPLETA|DIGITAL|IMPRESSO|VALIDADO"
    r"|MODELO|INTERPROX|TRACADO|KIT",
    re.I,
)


# ── Utilitários ───────────────────────────────────────────────────────────────

def slug(nome: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", nome).strip("_").lower()


def _sem_acentos(s: str) -> str:
    """Remove acentos/cedilha. A worklist do PRORADIS NÃO casa 'Ç'/acentos no
    filtro por nome (ex.: 'FRANÇA' acha 0; 'FRANCA' acha) -> buscamos as 2 formas."""
    import unicodedata
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _variantes_nome(nome: str) -> list:
    """Variantes de busca por nome (original + sem acentos), deduplicadas."""
    chave = re.sub(r"\s+", " ", nome or "").strip()
    out = []
    for v in (chave, _sem_acentos(chave)):
        if v and v not in out:
            out.append(v)
    return out


def parse_groups(html: str) -> dict:
    """
    Retorna {study_tail: desc}.
    Porta direta de _entrega.py: associa cada studyUID ao data-desc mais proximo anterior.
    """
    events = []
    for m in re.finditer(r'data-desc="([^"]+)"', html):
        events.append((m.start(), "desc", m.group(1)))
    for m in re.finditer(r"viewer/u\?studyUID=[\d.]+\.(\d+)", html):
        events.append((m.start(), "study", m.group(1)))
    events.sort()
    groups: dict = {}
    cur = None
    for _, kind, val in events:
        if kind == "desc":
            cur = val
        elif kind == "study" and cur is not None:
            groups.setdefault(val, cur)
    return groups


# ── Login / sessao ────────────────────────────────────────────────────────────

def _login_playwright(pw, email: str, password: str):
    """
    Faz login via Playwright e retorna (browser, ctx, page). Resiliente: tenta até
    3x (lentidão/hiccup do SmartRIS é intermitente). O submit usa no_wait_after
    (não bloqueia na navegação — quem confirma o login é o polling de URL).
    """
    last = None
    for tentativa in range(1, 4):
        browser = pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            ctx = browser.new_context(locale="pt-BR", timezone_id="America/Sao_Paulo")
            page = ctx.new_page()
            # domcontentloaded em vez de networkidle: o SmartRIS faz polling/websocket
            # que nunca "aquieta", então networkidle estoura timeout intermitente.
            page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=60000)
            page.fill('input[name="username"]', email)
            page.fill('input[name="password"]', password)
            # no_wait_after: o click dispara navegação que às vezes "pendura" 30s —
            # não esperamos por ela aqui; o polling de URL abaixo confirma o login.
            try:
                page.click('button[type="submit"], input[type="submit"]',
                           no_wait_after=True, timeout=15000)
            except Exception:
                pass  # mesmo se o click reclamar de navegação, segue pro polling
            ok = False
            for _ in range(120):  # até ~60s
                u = page.url
                if "/login" not in u and "checklogin" not in u:
                    ok = True
                    break
                page.wait_for_timeout(500)
            page.wait_for_timeout(1500)
            if ok and "/login" not in page.url and "checklogin" not in page.url:
                return browser, ctx, page
            last = RuntimeError(f"Falha no login — URL pos-submit: {page.url}")
        except Exception as e:
            last = e
        try:
            browser.close()
        except Exception:
            pass
        if tentativa < 3:
            time.sleep(4)  # backoff antes de tentar de novo
    raise last or RuntimeError("Falha no login PRORADIS após 3 tentativas")


def _is_logged_out(page) -> bool:
    """Detecta expiracao de sessao por URL ou HTML de login no corpo."""
    try:
        if "login" in page.url or "checklogin" in page.url:
            return True
        body = page.content()
        if 'name="username"' in body or 'name="password"' in body:
            return True
    except Exception:
        pass
    return False


# ── Worklist ──────────────────────────────────────────────────────────────────

def _parse_worklist_html(raw_html: str, by_acc: dict) -> None:
    """Parseia HTML da worklist e acumula em by_acc (mutação in-place)."""
    soup = BeautifulSoup(raw_html, "lxml")
    for tr in soup.find_all("tr"):
        h = str(tr)
        acc_m = re.search(r"\b(4\d{7})\b", h)
        if not acc_m:
            continue
        acc = acc_m.group(1)

        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        nome = ""
        for c in cells:
            if (
                re.match(r"^[A-ZÁÂÃÉÊÍÓÔÕÚÇ ]{10,55}$", c)
                and not _BAD_RE.search(c)
                and len(c) > len(nome)
            ):
                nome = c

        if acc not in by_acc:
            by_acc[acc] = {"accession": acc, "nome": nome or acc, "rows_html": []}
        if nome and len(nome) > len(by_acc[acc]["nome"]):
            by_acc[acc]["nome"] = nome
        # Evitar duplicar a mesma linha HTML
        if h not in by_acc[acc]["rows_html"]:
            by_acc[acc]["rows_html"].append(h)


def _get_relatorio_analitico(page, convenios: list, segmentos: list, data: str):
    """
    Obtém o relatório analítico REDE UNNA reutilizando a sessão Playwright ativa.
    Navega para admin_reports, lê tokens por-sessão, faz POST e retorna DataFrame.
    """
    import time as _time
    page.goto(f"{BASE}/admin_reports", wait_until="networkidle")
    _time.sleep(2)

    # Selecionar patients_detailed_report via JS (select usa Chosen/hidden)
    page.evaluate("""(function(){
        var s=document.querySelector('select[name="r1"]');
        if(!s) return;
        s.value='patients_detailed_report';
        s.dispatchEvent(new Event('change',{bubbles:true}));
        if(window.$) $(s).trigger('change');
    })()""")
    page.wait_for_load_state("networkidle")
    _time.sleep(4)

    # Ler tokens de convênio e segmento
    conv_map = {}
    for opt in page.query_selector_all('select[name="insurance"] option'):
        txt = opt.inner_text().strip()
        val = opt.get_attribute("value") or ""
        if txt and val:
            conv_map[txt] = val

    seg_map = {}
    for opt in page.query_selector_all('select[name="segments"] option'):
        txt = opt.inner_text().strip()
        val = opt.get_attribute("value") or ""
        if txt and val:
            seg_map[txt] = val

    ins_toks = resolve_tokens(convenios, conv_map, "convenio")
    seg_toks = resolve_tokens(segmentos, seg_map, "segmento")

    # Capturar cookies da sessão para o POST via requests
    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
    html_rel = post_relatorio(cookies, ins_toks, seg_toks, data, data)
    df, _, _ = parse_html_to_df(html_rel)
    return df


def listar_worklist_dia(page, data: str) -> list:
    """
    POST /ris/reports_list/get_list via JS fetch (mantem cookies de sessao).
    Faz duas queries — study_datetime e realized — e merge os resultados.
    Isso cobre o caso em que o relatorio analitico usa datetype=realized
    mas o study_datetime do exame e de outro dia.
    data: 'DD/MM/YYYY'
    Retorna: [{accession, nome, rows_html: [str]}, ...]
    """
    dt_inicio = f"{data} 00:00:00"
    dt_fim = f"{data} 23:59:59"

    # Query JS que aceita tipo_data como parametro
    _JS = """async ([inicio, fim, tipo]) => {
        const body = new URLSearchParams({
            'busca-por': 'name',
            'filtro[nome]': '',
            'filtro[exames]': 'todos',
            'filtro[tipo_data]': tipo,
            'optionsRadios': 'entre',
            'filtro_data_inicio': inicio,
            'filtro_data_fim': fim,
        });
        const _ac = new AbortController();
        const _to = setTimeout(() => _ac.abort(), 25000);
        let r;
        try {
            r = await fetch('/ris/reports_list/get_list', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: body.toString(),
                credentials: 'include',
                signal: _ac.signal
            });
        } finally { clearTimeout(_to); }
        return await r.text();
    }"""

    by_acc: dict = {}

    for tipo in ("study_datetime", "realized"):
        try:
            raw_html = page.evaluate(_JS, [dt_inicio, dt_fim, tipo])
            _parse_worklist_html(raw_html, by_acc)
        except Exception as e:
            print(f"   [worklist] falha com tipo={tipo}: {e}")

    return list(by_acc.values())


# JS: busca get_list por NOME + intervalo do dia (evita teto de resultados amplos)
_JS_WL_NOME = """async ([nome, inicio, fim, tipo]) => {
    const body = new URLSearchParams({
        'busca-por': 'name',
        'filtro[nome]': nome,
        'filtro[exames]': 'todos',
        'filtro[tipo_data]': tipo,
        'optionsRadios': 'entre',
        'filtro_data_inicio': inicio,
        'filtro_data_fim': fim,
    });
    const _ac = new AbortController();
    const _to = setTimeout(() => _ac.abort(), 25000);
    let r;
    try {
        r = await fetch('/ris/reports_list/get_list', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: body.toString(),
            credentials: 'include',
            signal: _ac.signal
        });
    } finally { clearTimeout(_to); }
    return await r.text();
}"""


def listar_worklist_por_pacientes(page, data: str, nomes: list) -> list:
    """
    Constroi a worklist do dia consultando por NOME de cada paciente + intervalo do dia.

    O endpoint get_list TRUNCA buscas amplas (responde "Muitos exames encontrados.
    Total: N" e devolve apenas a 1a pagina, ~81 linhas). Para dias com muitos exames
    isso deixava de fora a maioria dos accessions. Buscar por nome+dia mantem cada
    resposta pequena (bem abaixo do teto) e garante cobertura completa.
    data: 'DD/MM/YYYY'. Retorna: [{accession, nome, rows_html: [str]}, ...]
    """
    dt_inicio = f"{data} 00:00:00"
    dt_fim = f"{data} 23:59:59"
    by_acc: dict = {}
    vistos: set = set()
    for nome in nomes:
        for chave in _variantes_nome(nome):  # original + sem acentos (bug da cedilha)
            if chave.upper() in vistos:
                continue
            vistos.add(chave.upper())
            for tipo in ("study_datetime", "realized"):
                try:
                    raw_html = page.evaluate(_JS_WL_NOME, [chave, dt_inicio, dt_fim, tipo])
                    _parse_worklist_html(raw_html, by_acc)
                except Exception as e:
                    print(f"   [worklist] falha nome={chave} tipo={tipo}: {e}")
    return list(by_acc.values())


# ── Tokens ────────────────────────────────────────────────────────────────────

def extrair_tokens(row_html: str) -> dict:
    """
    Extrai tokens de uma linha HTML da worklist.
    Porta direta dos regex de _laudos_neuza.py + _entrega.py.
    Retorna: {pan, ceph, doc, exame}
    """
    h = row_html
    pan = re.findall(r"openReportPDF\(event,\s*'([^']+)'\)", h)
    ceph = re.findall(r"openReportPDFCeph\(event,\s*'([^']+)'\)", h)
    doc_m = re.search(
        r"base_url\('reports_doc'\),\s*'reports_doc',\s*"
        r"\{study_id\s*:\s*'([^']+)',\s*schedule_id:\s*'([^']+)'",
        h,
    )
    doc = {"study_id": doc_m.group(1), "schedule_id": doc_m.group(2)} if doc_m else None

    exame = ""
    h_upper = h.upper()
    for kw in EXAME_KEYWORDS:
        if kw in h_upper:
            exame = kw
            break

    return {"pan": pan, "ceph": ceph, "doc": doc, "exame": exame}


def extrair_exame_status(row_html: str) -> tuple:
    """Lê (exame, status) de uma linha da worklist.
    Status vem de <td name="report_status"><span class="tag">...</span></td>;
    exame de <span class="wrap-exam">. Retorna ('', '') se não achar."""
    soup = BeautifulSoup(row_html, "lxml")
    st_el = soup.select_one('td[name="report_status"] .tag') or \
        soup.select_one('td[name="report_status"]')
    status = st_el.get_text(strip=True) if st_el else ""
    ex_el = soup.select_one(".wrap-exam")
    exame = ex_el.get_text(strip=True) if ex_el else ""
    return exame, status


# ── Deteccao de imagem entregavel (logo RadioBras) ─────────────────────────────

def tem_logo_radiobras(body: bytes) -> bool:
    """
    True se a imagem JPEG tem a logo VERDE RadioBras no canto superior-esquerdo.
    Sinal deterministico de "imagem no padrao de entrega" (lamina com logo +
    cabecalho do paciente). Radiografias cruas e renders sao cinza puro (R=G=B):
    fracao de verde = 0 -> descartados. Apenas a logo introduz verde no cabecalho.
    100% por cor, SEM OCR/IA.
    """
    arr = np.frombuffer(body, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)  # BGR
    if img is None:
        return False
    H, W = img.shape[:2]
    # ROI = cabecalho superior-esquerdo, onde fica a logo em todas as laminas.
    roi = img[0: max(1, int(H * 0.18)), 0: max(1, int(W * 0.40))].astype(int)
    b, g, r = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
    green_mask = ((g - np.maximum(r, b)) > 25) & (g > 60)
    return float(green_mask.mean()) >= LOGO_GREEN_MIN_FRAC


def _ahash(body: bytes):
    """aHash 8x8 (inteiro 64-bit) para dedup perceptual. None se nao decodificar."""
    arr = np.frombuffer(body, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    g = cv2.resize(img, (8, 8), interpolation=cv2.INTER_AREA).astype(np.float64)
    bits = (g > g.mean()).flatten()
    h = 0
    for bdy in bits:
        h = (h << 1) | int(bdy)
    return h


def _eh_duplicata(h, seen_hashes: set) -> bool:
    """True se h e perceptualmente igual a algum hash ja visto (Hamming <= limiar)."""
    for prev in seen_hashes:
        if bin(h ^ prev).count("1") <= PHASH_DUP_MAX_HAMMING:
            return True
    return False


# ── Download de imagens ───────────────────────────────────────────────────────

def baixar_imagens(
    page,
    ctx,
    study_id: str,
    schedule_id: str,
    out_dir: str,
    seen_hashes: set,
    start_n: int,
) -> dict:
    """
    Porta _entrega.py: intercepta viewer/u/image, abre popup reports_doc,
    salva TODAS as imagens no padrao de entrega (logo RadioBras + cabecalho).

    O criterio NAO e mais o grupo "DOCUMENTACAO COMPLETA" nem a dimensao: capturamos
    qualquer JPEG carregado pelo composer e ficamos com os que tem a logo RadioBras
    (deteccao deterministica via tem_logo_radiobras). Isso pega laminas de panoramica
    avulsa (ex.: Geovane) e descarta radiografias cruas e renders 3D.

    seen_hashes: set compartilhado no escopo do paciente (dedup global por conteudo).
    start_n: contador inicial (continua a numeracao ENTREGA_N entre chamadas).
    Retorna: {qtd, arquivos, pendencias, next_n, total_capturadas}
    """
    captured: list = []  # [(tail, body)]
    pendencias: list = []

    def on_resp(r):
        if "viewer/u/image" not in r.url:
            return
        try:
            body = r.body()
        except Exception:
            return
        if body[:2] == b"\xff\xd8":
            q = dict(re.findall(r"[?&]([^=&]+)=([^&]+)", r.url))
            tail = q.get("studyUID", "").split(".")[-1]
            captured.append((tail, body))

    _ABRE_POPUP = """([s, sc]) => {
        const f = document.createElement('form');
        f.method = 'POST';
        f.action = '/ris/reports_doc';
        f.target = 'docpop';
        for (const [k, v] of [['study_id', s], ['schedule_id', sc]]) {
            const i = document.createElement('input');
            i.name = k; i.value = v;
            f.appendChild(i);
        }
        document.body.appendChild(f);
        window.open('', 'docpop');
        f.submit();
    }"""

    ctx.on("response", on_resp)
    try:
        # Abrir popup (form POST target=docpop) para disparar o carregamento das
        # imagens. O viewer às vezes demora a abrir -> timeout maior + retry.
        popup = None
        for tentativa in range(POPUP_OPEN_RETRIES + 1):
            try:
                with ctx.expect_page(timeout=EXPECT_PAGE_MS) as pinfo:
                    page.evaluate(_ABRE_POPUP, [study_id, schedule_id])
                popup = pinfo.value
                break
            except Exception as e:
                if tentativa >= POPUP_OPEN_RETRIES:
                    pendencias.append(f"popup nao abriu (study {study_id}): {e}")
                else:
                    page.wait_for_timeout(2000)

        if popup is not None:
            try:
                popup.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            popup.wait_for_timeout(POPUP_WAIT_MS)
            try:
                popup.close()
            except Exception:
                pass

    finally:
        ctx.remove_listener("response", on_resp)

    # Salvar imagens no padrao de entrega: deteccao de logo + dedup perceptual.
    # seen_hashes guarda aHashes (int) das imagens ja salvas no escopo do paciente.
    n = start_n
    arquivos: list = []
    salvos = 0
    total_capturadas = 0
    for tail, body in captured:
        total_capturadas += 1
        # Criterio deterministico: so entregaveis tem a logo VERDE RadioBras.
        if not tem_logo_radiobras(body):
            continue
        h = _ahash(body)
        if h is None:
            continue
        # Dedup perceptual: o viewer re-codifica a mesma lamina (md5 difere).
        if _eh_duplicata(h, seen_hashes):
            continue
        seen_hashes.add(h)
        n += 1
        salvos += 1
        fname = f"ENTREGA_{n}.jpg"
        with open(os.path.join(out_dir, fname), "wb") as f:
            f.write(body)
        arquivos.append(fname)

    return {
        "qtd": salvos, "arquivos": arquivos, "pendencias": pendencias,
        "next_n": n, "total_capturadas": total_capturadas,
    }


# ── Download de laudos ────────────────────────────────────────────────────────

def baixar_laudos(page, ctx, tokens_list: list, out_dir: str) -> list:
    """
    Porta _laudos_neuza.py: ceph via render Playwright; comuns via requests.
    tokens_list: lista de dicts retornados por extrair_tokens().
    Retorna: [{exame, arquivo, bytes, status}, ...]
    """
    cj = {c["name"]: c["value"] for c in ctx.cookies()}
    sess = requests.Session()
    sess.cookies.update(cj)
    sess.headers.update({"User-Agent": "Mozilla/5.0", "Referer": f"{BASE}/reports_list"})

    resultados: list = []
    usados: set = set()
    seen_tokens: set = set()    # dedup barato: mesmo token = mesmo PDF
    seen_content: set = set()   # dedup robusto (pan): tokens distintos, mesmo conteudo
    seen_ceph_keys: set = set()  # dedup ceph por (acc, exame): render e nao-deterministico

    def _nome_unico(base: str) -> str:
        fname = base
        idx = 1
        while fname in usados:
            idx += 1
            fname = base.replace(".pdf", f"_{idx}.pdf")
        usados.add(fname)
        return fname

    # --- Ceph via render Playwright (pagina isolada) ---
    for tok_info in tokens_list:
        exame = tok_info["exame"] or "EXAME"
        acc = tok_info.get("acc", "")
        for tok in tok_info["ceph"]:
            if tok in seen_tokens:
                continue
            seen_tokens.add(tok)
            # Um laudo oficial por exame: o render via page.pdf() nao e
            # byte-deterministico (embute timestamp), entao deduplicamos pela
            # chave logica (accession, exame) antes de renderizar.
            ceph_key = (acc, exame)
            if ceph_key in seen_ceph_keys:
                continue
            ceph_page = ctx.new_page()
            try:
                url = f"{BASE}/report_ceph/print_preview/{tok}"
                ceph_page.goto(url, wait_until="networkidle")
                ceph_page.wait_for_timeout(3500)
                ceph_page.evaluate(
                    "() => { document.querySelectorAll"
                    "('#bg-loading,.loading,#loading').forEach(e => e.remove()); }"
                )
                pdf_bytes = ceph_page.pdf(format="A4", print_background=True)
                size = len(pdf_bytes)
                if size < 10_000:  # ≈857B = pagina de erro renderizada
                    resultados.append(
                        {"exame": exame, "arquivo": None, "bytes": size, "status": "NAO_PRONTO"}
                    )
                else:
                    seen_ceph_keys.add(ceph_key)  # exame ja entregue; ignora tokens repetidos
                    fname = _nome_unico(f"LAUDO_{exame}_{acc}_CEPH.pdf")
                    with open(os.path.join(out_dir, fname), "wb") as f:
                        f.write(pdf_bytes)
                    resultados.append(
                        {"exame": exame, "arquivo": fname, "bytes": size, "status": "OK"}
                    )
            except Exception as e:
                resultados.append(
                    {"exame": exame, "arquivo": None, "bytes": 0, "status": "ERRO", "detalhe": str(e)}
                )
            finally:
                try:
                    ceph_page.close()
                except Exception:
                    pass

    # --- Laudos comuns via requests GET (cookies da sessao) ---
    for tok_info in tokens_list:
        exame = tok_info["exame"] or "EXAME"
        acc = tok_info.get("acc", "")
        for tok in tok_info["pan"]:
            if tok in seen_tokens:
                continue
            seen_tokens.add(tok)
            try:
                r = sess.get(f"{BASE}/report/pdf?studies={tok}", timeout=60)
                if r.content[:4] == b"%PDF":
                    ch = hashlib.md5(r.content).hexdigest()
                    if ch in seen_content:
                        continue  # mesmo laudo ja salvo (token diferente, conteudo igual)
                    seen_content.add(ch)
                    fname = _nome_unico(f"LAUDO_{exame}_{acc}_OFICIAL.pdf")
                    with open(os.path.join(out_dir, fname), "wb") as f:
                        f.write(r.content)
                    resultados.append(
                        {"exame": exame, "arquivo": fname, "bytes": len(r.content), "status": "OK"}
                    )
                else:
                    resultados.append(
                        {"exame": exame, "arquivo": None, "bytes": len(r.content), "status": "NAO_PRONTO"}
                    )
            except Exception as e:
                resultados.append(
                    {"exame": exame, "arquivo": None, "bytes": 0, "status": "ERRO", "detalhe": str(e)}
                )

    return resultados


# ── Fallback: busca por nome sem filtro de data ───────────────────────────────

def _buscar_na_worklist_por_nome(page, nome: str, acc: str, data: str) -> dict | None:
    """
    Fallback por accession nao localizado: busca o get_list pelo nome completo do
    paciente + intervalo do dia (optionsRadios='entre') e localiza a linha com o
    accession. Nome+dia mantem a resposta abaixo do teto do servidor.
    Retorna dict {accession, nome, rows_html} ou None.
    """
    variantes = _variantes_nome(nome)
    if not variantes:
        return None
    dt_inicio = f"{data} 00:00:00"
    dt_fim = f"{data} 23:59:59"
    rows_html = []
    for chave in variantes:  # original + sem acentos (bug da cedilha na worklist)
        for tipo in ("study_datetime", "realized"):
            try:
                raw = page.evaluate(_JS_WL_NOME, [chave, dt_inicio, dt_fim, tipo])
            except Exception:
                continue
            soup = BeautifulSoup(raw, "lxml")
            for tr in soup.find_all("tr"):
                h = str(tr)
                if acc in h and h not in rows_html:
                    rows_html.append(h)
            if rows_html:
                break
        if rows_html:
            break

    if not rows_html:
        return None
    return {"accession": acc, "nome": nome, "rows_html": rows_html}


# ── Processamento por paciente ────────────────────────────────────────────────

def _nome_paciente_row(row_html: str) -> str:
    """
    Nome do paciente de uma linha da worklist. O 1o <span class="wrap-name"> e o
    paciente (o 2o e o solicitante). Normalizado para comparacao (uppercase, espacos).
    """
    m = re.search(r'class="wrap-name">\s*([^<]+?)\s*</span>', row_html)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip().upper()


def _expandir_accessions_por_nome(accessions: list, worklist: list) -> tuple:
    """
    Fase 1: o relatorio analitico define QUAIS pacientes processar, mas pode OMITIR
    exames (ex.: panoramica ja 'Impressa' nao faturada). Aqui unimos TODOS os exames
    do dia (da worklist ja restrita ao dia) que pertencem ao mesmo paciente — casados
    pelo nome exato na worklist (consistente dentro do dia). Sem vazar datas antigas.
    Retorna: (accessions_expandidas, accessions_extras).
    """
    wl_by_acc = {w["accession"]: w for w in worklist}
    # Nomes (worklist) dos exames faturados que conseguimos localizar
    nomes_alvo = set()
    for acc in accessions:
        w = wl_by_acc.get(acc)
        if not w:
            continue
        for h in w["rows_html"]:
            nm = _nome_paciente_row(h)
            if nm:
                nomes_alvo.add(nm)
    if not nomes_alvo:
        return list(accessions), []
    # Varre a worklist do dia e adiciona accessions do mesmo paciente
    extras = []
    base = set(accessions)
    for w in worklist:
        if w["accession"] in base:
            continue
        if any(_nome_paciente_row(h) in nomes_alvo for h in w["rows_html"]):
            extras.append(w["accession"])
    return list(accessions) + extras, extras


def _processar_paciente(page, ctx, pac: dict, worklist: list, zip_root: str, data: str) -> dict:
    """
    Processa UM paciente (agrupado por Cod. Pac) dentro de uma sessao Playwright.
    Agrega todos os N exames (accessions) do paciente numa unica pasta.
    """
    nome = pac["nome"]
    cod = pac["cod_pac"]
    # Fase 1: expandir para todos os exames do dia do paciente (panoramica omitida
    # pelo analitico, etc.), casando por nome dentro da worklist ja restrita ao dia.
    accessions, _extras = _expandir_accessions_por_nome(pac["accessions"], worklist)
    pasta_nome = f"{slug(nome)}_{cod}"
    out_dir = os.path.join(zip_root, pasta_nome)
    os.makedirs(out_dir, exist_ok=True)

    resultado = {
        "nome": nome,
        "cod_pac": cod,
        "convenio": pac.get("convenio", ""),
        "accessions": accessions,
        "pasta": pasta_nome,
        "status": "PENDENTE",
        "imagens": {"qtd": 0, "arquivos": []},
        "laudos": [],
        "pendencias": [],
        "notas": [],
    }

    # Localizar todas as linhas (de todas as accessions) do paciente
    wl_by_acc = {w["accession"]: w for w in worklist}
    tokens_list: list = []  # cada item: dict de extrair_tokens + 'acc'
    nao_localizadas: list = []
    for acc in accessions:
        wl_pac = wl_by_acc.get(acc)
        if not wl_pac:
            # Fallback: busca por nome sem filtro de data (mismatch realized vs study_datetime)
            wl_pac = _buscar_na_worklist_por_nome(page, nome, acc, data)
            if wl_pac:
                resultado["notas"].append(
                    f"accession {acc} localizado via fallback por nome"
                )
        if not wl_pac:
            nao_localizadas.append(acc)
            continue
        for h in wl_pac["rows_html"]:
            t = extrair_tokens(h)
            t["acc"] = acc
            tokens_list.append(t)

    if _extras:
        resultado["notas"].append(
            "exame(s) do dia incluidos alem do analitico: " + ", ".join(_extras)
        )

    if nao_localizadas:
        resultado["pendencias"].append(
            "accessions nao localizadas na worklist: " + ", ".join(nao_localizadas)
        )

    if not tokens_list:
        resultado["status"] = "ERRO"
        resultado["pendencias"].append("nenhuma linha localizada na worklist")
        return resultado

    # Download de imagens — uma unica chamada reports_doc basta (retorna TODOS os
    # grupos do paciente). Tentar docs em ordem ate capturar; parar no 1o sucesso.
    docs = [t["doc"] for t in tokens_list if t["doc"]]
    if docs:
        seen_hashes: set = set()
        n = 0
        arquivos: list = []
        img_pendencias: list = []
        for d in docs:
            try:
                img_res = baixar_imagens(
                    page, ctx, d["study_id"], d["schedule_id"], out_dir, seen_hashes, n
                )
            except Exception as e:
                img_pendencias.append(f"erro imagens (study {d['study_id']}): {e}")
                continue
            n = img_res["next_n"]
            arquivos.extend(img_res["arquivos"])
            if img_res["qtd"] > 0:
                break  # reports_doc retorna todos os grupos -> uma chamada basta
        resultado["imagens"] = {"qtd": n, "arquivos": arquivos}
        # Imagens sao best-effort: capturamos TODAS no padrao de entrega (logo).
        # Ausencia de imagem entregavel e nota, nao pendencia (o laudo e o entregavel
        # obrigatorio). Ex.: panoramica sem lamina gerada ainda.
        if n == 0:
            resultado["notas"].append("sem imagens no padrao de entrega (logo+cabecalho)")
        resultado["pendencias"].extend(img_pendencias)
    else:
        resultado["pendencias"].append("sem token reports_doc na worklist")

    # Download de laudos (por accession; nome de arquivo unico)
    try:
        laudos = baixar_laudos(page, ctx, tokens_list, out_dir)
        resultado["laudos"] = laudos
        for lau in laudos:
            if lau["status"] == "NAO_PRONTO":
                resultado["pendencias"].append(f"laudo {lau['exame']} nao pronto")
            elif lau["status"] == "ERRO":
                resultado["pendencias"].append(
                    f"laudo {lau['exame']} erro: {lau.get('detalhe', '')}"
                )
    except Exception as e:
        resultado["pendencias"].append(f"erro laudos: {e}")

    # Status final — 'notas' NAO contam como pendencia. As imagens sao best-effort
    # (capturamos todas no padrao de entrega); o entregavel OBRIGATORIO e o laudo.
    laudos = resultado["laudos"]
    tem_laudo = len(laudos) > 0
    laudos_ok = all(l["status"] == "OK" for l in laudos) if laudos else False
    if not tem_laudo and "sem token reports_doc na worklist" not in resultado["pendencias"]:
        resultado["pendencias"].append("nenhum laudo disponivel")
    if tem_laudo and laudos_ok and not resultado["pendencias"]:
        resultado["status"] = "OK"
    elif resultado["status"] != "ERRO":
        resultado["status"] = "PENDENTE"

    return resultado


# ── Relatorio texto ───────────────────────────────────────────────────────────

def _gerar_txt(relatorio: dict) -> str:
    r = relatorio
    lines = [
        "RELATORIO DE EXTRACAO — REDE UNNA",
        (
            f"Periodo: {r['periodo']['de']} a {r['periodo']['ate']}"
            f"   |   Gerado: {r['gerado_em']}"
        ),
        (
            f"Pacientes: {r['resumo']['pacientes_total']}"
            f"   |   OK: {r['resumo']['ok_completo']}"
            f"   |   Pendentes: {r['resumo']['com_pendencia']}"
            f"   |   Erros: {r['resumo']['com_erro']}"
        ),
        "-" * 60,
    ]
    for pac in r["pacientes"]:
        status = pac["status"]
        tag = f"[{status:<8}]"
        cod = pac.get("cod_pac", pac.get("accession", ""))
        accs = ", ".join(pac.get("accessions", []))
        ident = f"{cod}" + (f" | exames: {accs}" if accs else "")
        lines.append(f"{tag} {pac['nome']} ({ident})")
        qtd = pac["imagens"]["qtd"]
        laudos_parts = []
        for lau in pac["laudos"]:
            tipo = "(ceph)" if "CEPH" in (lau.get("arquivo") or "") else ""
            laudos_parts.append(f"{lau['exame']}{tipo} {lau['status']}")
        detalhe = " | ".join([f"imagens={qtd}"] + laudos_parts)
        lines.append(f"           {detalhe}")
        for pend in pac.get("pendencias", []):
            lines.append(f"           !! {pend}")
        for nota in pac.get("notas", []):
            lines.append(f"           -- {nota}")
    return "\n".join(lines) + "\n"


# ── Orquestrador principal ────────────────────────────────────────────────────

def processar_dia(
    data: str,
    convenios: list,
    segmentos: list,
    progress_cb=None,
) -> tuple:
    """
    Extrai imagens e laudos de todos os pacientes REDE UNNA do dia.
    data: 'DD/MM/YYYY'
    progress_cb: callable(msg: str) opcional.
    Retorna: (zip_bytes: bytes, relatorio: dict)
    """

    def log(msg: str):
        print(msg)
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    log(f"\n=== processar_dia {data} | {len(convenios)} convenios ===")
    email, password = get_credentials()

    # Sessao Playwright UNICA para o job inteiro: 1 login (re-login so se cair).
    pacientes: list = []
    resultados: list = []

    job_ts = datetime.now().strftime("%Y%m%d%H%M%S")
    zip_root = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), f"_tmp_{job_ts}"
    )
    os.makedirs(zip_root, exist_ok=True)

    try:
        with sync_playwright() as pw:
            browser, ctx, page = _login_playwright(pw, email, password)
            try:
                # Relatorio analitico REDE UNNA (mesma sessao do login)
                log("[A] Relatorio analitico REDE UNNA (mesma sessao)...")
                df = _get_relatorio_analitico(page, convenios, segmentos, data)
                if df.empty:
                    raise ValueError(f"Nenhum paciente REDE UNNA em {data}.")
                cod_col = "Cód. Pac" if "Cód. Pac" in df.columns else df.columns[1]
                pedido_col = "Pedido" if "Pedido" in df.columns else df.columns[6]
                nome_col = "Paciente" if "Paciente" in df.columns else df.columns[2]
                conv_col = (
                    "Convênio" if "Convênio" in df.columns
                    else (df.columns[5] if len(df.columns) > 5 else None)
                )

                def _conv_unidade(texto: str) -> str:
                    """Normaliza a célula 'Convênio' para o nome da unidade selecionada
                    (ex.: 'REDE UNNA - CENTRO / PLANO X' -> 'REDE UNNA - CENTRO')."""
                    t = (texto or "").strip()
                    up = t.upper()
                    for c in convenios:
                        if c.upper() in up:
                            return c
                    return t.split(" / ")[0].strip() or "SEM CONVENIO"

                # Agrupar por Cod. Pac (um paciente -> N exames/accessions)
                by_cod: dict = {}
                for _, row in df.iterrows():
                    cod = str(row[cod_col]).strip()
                    acc = str(row[pedido_col]).strip()
                    nome = str(row[nome_col]).strip()
                    conv = _conv_unidade(str(row[conv_col])) if conv_col else ""
                    if not cod:
                        cod = acc  # chave de fallback
                    if cod not in by_cod:
                        by_cod[cod] = {
                            "cod_pac": cod, "nome": nome, "accessions": [],
                            "convenio": conv,
                        }
                    if acc and acc not in by_cod[cod]["accessions"]:
                        by_cod[cod]["accessions"].append(acc)
                    if len(nome) > len(by_cod[cod]["nome"]):
                        by_cod[cod]["nome"] = nome
                    if conv and not by_cod[cod].get("convenio"):
                        by_cod[cod]["convenio"] = conv
                pacientes.extend(by_cod.values())
                pacientes.sort(key=lambda x: x["cod_pac"])
                log(f"   {len(pacientes)} pacientes unicos (agrupados por Cod. Pac).")

                # Processar em lotes na MESMA sessao (lote = escopo da worklist + log)
                i = 0
                while i < len(pacientes):
                    lote = pacientes[i: i + BATCH_SIZE]
                    log(f"\n[LOTE {i // BATCH_SIZE + 1}] {i + 1}–{i + len(lote)} / {len(pacientes)}")

                    try:
                        if _is_logged_out(page):
                            log("    [re-login]")
                            try:
                                browser.close()
                            except Exception:
                                pass
                            browser, ctx, page = _login_playwright(pw, email, password)
                        worklist = listar_worklist_por_pacientes(
                            page, data, [p["nome"] for p in lote]
                        )
                        log(f"   Worklist: {len(worklist)} accessions")
                    except Exception as e:
                        log(f"   Falha worklist: {e}")
                        for pac in lote:
                            resultados.append({
                                "nome": pac["nome"],
                                "cod_pac": pac["cod_pac"],
                                "convenio": pac.get("convenio", ""),
                                "accessions": pac["accessions"],
                                "pasta": f"{slug(pac['nome'])}_{pac['cod_pac']}",
                                "status": "ERRO",
                                "imagens": {"qtd": 0, "arquivos": []},
                                "laudos": [],
                                "pendencias": [f"falha worklist: {e}"],
                                "notas": [],
                            })
                        i += BATCH_SIZE
                        continue

                    for pac in lote:
                        log(f"  -> {pac['nome']} ({pac['cod_pac']})")
                        retries = 0
                        while retries <= MAX_RETRIES:
                            try:
                                if _is_logged_out(page):
                                    log("    [re-login]")
                                    try:
                                        browser.close()
                                    except Exception:
                                        pass
                                    browser, ctx, page = _login_playwright(pw, email, password)
                                    worklist = listar_worklist_por_pacientes(
                                        page, data, [p["nome"] for p in lote]
                                    )

                                res = _processar_paciente(page, ctx, pac, worklist, zip_root, data)
                                resultados.append(res)
                                log(
                                    f"    {res['status']} imgs={res['imagens']['qtd']}"
                                    f" laudos={len(res['laudos'])}"
                                )
                                break

                            except Exception as e:
                                retries += 1
                                log(f"    tentativa {retries} falhou: {e}")
                                if retries > MAX_RETRIES:
                                    resultados.append({
                                        "nome": pac["nome"],
                                        "cod_pac": pac["cod_pac"],
                                        "convenio": pac.get("convenio", ""),
                                        "accessions": pac["accessions"],
                                        "pasta": f"{slug(pac['nome'])}_{pac['cod_pac']}",
                                        "status": "ERRO",
                                        "imagens": {"qtd": 0, "arquivos": []},
                                        "laudos": [],
                                        "pendencias": [
                                            f"erro apos {MAX_RETRIES} tentativas: {e}"
                                        ],
                                        "notas": [],
                                    })
                                else:
                                    time.sleep(3)
                    i += BATCH_SIZE
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

        # C: relatorio
        ok = sum(1 for r in resultados if r["status"] == "OK")
        pend = sum(1 for r in resultados if r["status"] == "PENDENTE")
        erros = sum(1 for r in resultados if r["status"] == "ERRO")

        relatorio = {
            "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "periodo": {"de": data, "ate": data},
            "convenios": convenios,
            "resumo": {
                "pacientes_total": len(resultados),
                "ok_completo": ok,
                "com_pendencia": pend,
                "com_erro": erros,
            },
            "pacientes": resultados,
        }
        rel_json = json.dumps(relatorio, ensure_ascii=False, indent=2)
        rel_txt = _gerar_txt(relatorio)

        # D: ZIP em memoria
        log("\n[ZIP] Empacotando...")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("relatorio.json", rel_json.encode("utf-8"))
            zf.writestr("RELATORIO.txt", rel_txt.encode("utf-8"))
            for res in resultados:
                pasta_path = os.path.join(zip_root, res["pasta"])
                if not os.path.isdir(pasta_path):
                    continue
                # Agrupar no ZIP por unidade/convênio: <convenio>/<paciente>/<arquivo>
                conv_dir = slug(res.get("convenio") or "sem_convenio") or "sem_convenio"
                for fname in sorted(os.listdir(pasta_path)):
                    fpath = os.path.join(pasta_path, fname)
                    if os.path.isfile(fpath):
                        zf.write(
                            fpath,
                            arcname=os.path.join(conv_dir, res["pasta"], fname),
                        )

        buf.seek(0)
        zip_bytes = buf.getvalue()
        log(f"[OK] ZIP montado ({len(zip_bytes):,} bytes)")
        return zip_bytes, relatorio

    finally:
        shutil.rmtree(zip_root, ignore_errors=True)
