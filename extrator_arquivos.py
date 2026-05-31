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

import requests
from bs4 import BeautifulSoup
from PIL import Image
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
# Imagens entregaveis sao de alta resolucao (min(w,h) >= 3024 nos dados observados);
# renders 3D MODELO DIGITAL sao sempre 1920x1080 (min=1080). Descartar abaixo deste
# limiar elimina os renders 3D de forma deterministica (gap 1920 <-> 3024).
MIN_DIM_ENTREGAVEL = 2000

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
    Faz login via Playwright e retorna (browser, ctx, page).
    Usa wait_for_url + wait_for_timeout conforme spec (nao networkidle pos-login).
    """
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.fill('input[name="username"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"], input[type="submit"]')
    # Aguarda navegação pós-login: networkidle é mais robusto que wait_for_url
    # quando o redirect passa por intermediários
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_timeout(1500)
    if "/login" in page.url or "checklogin" in page.url:
        browser.close()
        raise RuntimeError(f"Falha no login — URL pos-submit: {page.url}")
    return browser, ctx, page


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
        const r = await fetch('/ris/reports_list/get_list', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: body.toString(),
            credentials: 'include'
        });
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
    salva so as imagens entregaveis do grupo DOCUMENTACAO COMPLETA.

    seen_hashes: set compartilhado no escopo do paciente (dedup global por conteudo).
    start_n: contador inicial (continua a numeracao ENTREGA_N entre chamadas).
    Filtra renders 3D por resolucao (min(w,h) < MIN_DIM_ENTREGAVEL).
    Retorna: {qtd, arquivos, pendencias, next_n}
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

    ctx.on("response", on_resp)
    try:
        # POST reports_doc -> HTML dos grupos
        doc_html = page.evaluate(
            """async ([s, sc]) => {
                const fd = new URLSearchParams();
                fd.append('study_id', s);
                fd.append('schedule_id', sc);
                const r = await fetch('/ris/reports_doc', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                    body: fd.toString(),
                    credentials: 'include'
                });
                return await r.text();
            }""",
            [study_id, schedule_id],
        )

        groups = parse_groups(doc_html)
        deliver_tails = {
            k
            for k, v in groups.items()
            if re.search(r"DOCUMENTA[CÇ][AÃ]O.*COMPLETA", v, re.I)
        }

        if not deliver_tails:
            pendencias.append("sem grupo DOCUMENTACAO COMPLETA")
            return {
                "qtd": 0, "arquivos": [], "pendencias": pendencias,
                "next_n": start_n, "had_group": False,
            }

        # Abrir popup (form POST target=docpop) para disparar carregamento das imagens
        with ctx.expect_page() as pinfo:
            page.evaluate(
                """([s, sc]) => {
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
                }""",
                [study_id, schedule_id],
            )

        popup = pinfo.value
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

    # Salvar imagens entregaveis: dedup global por conteudo + filtro de resolucao
    n = start_n
    arquivos: list = []
    salvos = 0
    for tail, body in captured:
        if tail not in deliver_tails:
            continue
        h = hashlib.md5(body).hexdigest()
        if h in seen_hashes:
            continue
        # Filtro deterministico: descartar renders 3D (baixa resolucao)
        try:
            w, hgt = Image.open(io.BytesIO(body)).size
        except Exception:
            continue  # nao e imagem valida
        if min(w, hgt) < MIN_DIM_ENTREGAVEL:
            continue
        seen_hashes.add(h)
        n += 1
        salvos += 1
        fname = f"ENTREGA_{n}.jpg"
        with open(os.path.join(out_dir, fname), "wb") as f:
            f.write(body)
        arquivos.append(fname)

    if salvos == 0 and deliver_tails:
        pendencias.append("imagens nao prontas (grupo encontrado mas sem JPEG entregavel)")

    return {
        "qtd": salvos, "arquivos": arquivos, "pendencias": pendencias,
        "next_n": n, "had_group": True,
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

def _buscar_na_worklist_por_nome(page, nome: str, acc: str) -> dict | None:
    """
    Fallback para pacientes cujo study_datetime difere da data de realização.
    Busca pelo primeiro nome do paciente sem filtro de data e localiza a linha
    que contém o accession específico.
    Retorna dict {accession, nome, rows_html} ou None.
    """
    # Usar apenas o primeiro nome para a busca (mais tolerante a variações)
    primeiro_nome = nome.split()[0] if nome else ""
    if not primeiro_nome:
        return None
    try:
        raw = page.evaluate(
            """async (nome) => {
                const body = new URLSearchParams({
                    'busca-por': 'name',
                    'filtro[nome]': nome,
                    'filtro[exames]': 'todos',
                    'filtro[tipo_data]': 'study_datetime',
                    'optionsRadios': 'todos',
                });
                const r = await fetch('/ris/reports_list/get_list', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                    body: body.toString(),
                    credentials: 'include'
                });
                return await r.text();
            }""",
            primeiro_nome,
        )
    except Exception:
        return None

    soup = BeautifulSoup(raw, "lxml")
    rows_html = []
    for tr in soup.find_all("tr"):
        h = str(tr)
        if acc in h:
            rows_html.append(h)

    if not rows_html:
        return None
    return {"accession": acc, "nome": nome, "rows_html": rows_html}


# ── Processamento por paciente ────────────────────────────────────────────────

def _processar_paciente(page, ctx, pac: dict, worklist: list, zip_root: str) -> dict:
    """
    Processa UM paciente (agrupado por Cod. Pac) dentro de uma sessao Playwright.
    Agrega todos os N exames (accessions) do paciente numa unica pasta.
    """
    nome = pac["nome"]
    cod = pac["cod_pac"]
    accessions = pac["accessions"]
    pasta_nome = f"{slug(nome)}_{cod}"
    out_dir = os.path.join(zip_root, pasta_nome)
    os.makedirs(out_dir, exist_ok=True)

    resultado = {
        "nome": nome,
        "cod_pac": cod,
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
            wl_pac = _buscar_na_worklist_por_nome(page, nome, acc)
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
    had_group = False
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
            if img_res.get("had_group"):
                had_group = True
            if img_res["qtd"] > 0:
                break  # reports_doc retorna todos os grupos -> uma chamada basta
        resultado["imagens"] = {"qtd": n, "arquivos": arquivos}
        # So e pendencia se HAVIA grupo DOCUMENTACAO COMPLETA mas as imagens nao
        # foram capturadas. Exames sem grupo (ex.: panoramica avulsa) nao tem
        # fotos entregaveis -> nao e pendencia.
        if had_group and n == 0:
            resultado["pendencias"].append("imagens nao prontas (grupo presente, sem JPEG)")
        elif not had_group:
            resultado["notas"].append("exame(s) sem grupo de documentacao (sem fotos entregaveis)")
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

    # Status final — 'notas' NAO contam como pendencia.
    # Fotos so sao exigidas quando o paciente tem grupo DOCUMENTACAO COMPLETA.
    laudos = resultado["laudos"]
    tem_laudo = len(laudos) > 0
    laudos_ok = all(l["status"] == "OK" for l in laudos) if laudos else False
    img_ok = (resultado["imagens"]["qtd"] > 0) if had_group else True
    if not tem_laudo and "sem token reports_doc na worklist" not in resultado["pendencias"]:
        resultado["pendencias"].append("nenhum laudo disponivel")
    if img_ok and tem_laudo and laudos_ok and not resultado["pendencias"]:
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

    # Pacientes serão carregados dentro da 1ª sessão Playwright (evita 2 logins)
    pacientes: list = []
    _relatorio_carregado = False

    # B: processar em lotes
    job_ts = datetime.now().strftime("%Y%m%d%H%M%S")
    zip_root = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), f"_tmp_{job_ts}"
    )
    os.makedirs(zip_root, exist_ok=True)

    resultados: list = []
    i = 0

    try:
        # Primeiro lote carrega também o relatório analítico na mesma sessão
        while not _relatorio_carregado or i < len(pacientes):
            if not _relatorio_carregado:
                lote = []  # ainda não temos pacientes; carrega a lista primeiro
            else:
                lote = pacientes[i: i + BATCH_SIZE]
                log(f"\n[LOTE {i // BATCH_SIZE + 1}] {i + 1}–{i + len(lote)} / {len(pacientes)}")

            with sync_playwright() as pw:
                browser = ctx = page = None
                worklist = []

                try:
                    browser, ctx, page = _login_playwright(pw, email, password)

                    # 1ª sessão: carregar relatório analítico antes da worklist
                    if not _relatorio_carregado:
                        log("[A] Relatorio analitico REDE UNNA (mesma sessao)...")
                        df = _get_relatorio_analitico(page, convenios, segmentos, data)
                        if df.empty:
                            raise ValueError(f"Nenhum paciente REDE UNNA em {data}.")
                        cod_col = "Cód. Pac" if "Cód. Pac" in df.columns else df.columns[1]
                        pedido_col = "Pedido" if "Pedido" in df.columns else df.columns[6]
                        nome_col = "Paciente" if "Paciente" in df.columns else df.columns[2]
                        # Agrupar por Cod. Pac (um paciente -> N exames/accessions)
                        by_cod: dict = {}
                        for _, row in df.iterrows():
                            cod = str(row[cod_col]).strip()
                            acc = str(row[pedido_col]).strip()
                            nome = str(row[nome_col]).strip()
                            if not cod:
                                cod = acc  # chave de fallback
                            if cod not in by_cod:
                                by_cod[cod] = {"cod_pac": cod, "nome": nome, "accessions": []}
                            if acc and acc not in by_cod[cod]["accessions"]:
                                by_cod[cod]["accessions"].append(acc)
                            if len(nome) > len(by_cod[cod]["nome"]):
                                by_cod[cod]["nome"] = nome
                        pacientes.extend(by_cod.values())
                        pacientes.sort(key=lambda x: x["cod_pac"])
                        _relatorio_carregado = True
                        log(f"   {len(pacientes)} pacientes unicos (agrupados por Cod. Pac).")
                        lote = pacientes[i: i + BATCH_SIZE]
                        log(f"\n[LOTE 1] 1–{len(lote)} / {len(pacientes)}")

                    worklist = listar_worklist_dia(page, data)
                    log(f"   Worklist: {len(worklist)} accessions")
                except Exception as e:
                    log(f"   Falha login/relatorio/worklist: {e}")
                    if not _relatorio_carregado:
                        raise  # sem lista de pacientes, não tem como continuar
                    for pac in lote:
                        resultados.append({
                            "nome": pac["nome"],
                            "cod_pac": pac["cod_pac"],
                            "accessions": pac["accessions"],
                            "pasta": f"{slug(pac['nome'])}_{pac['cod_pac']}",
                            "status": "ERRO",
                            "imagens": {"qtd": 0, "arquivos": []},
                            "laudos": [],
                            "pendencias": [f"falha login/worklist: {e}"],
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
                                worklist = listar_worklist_dia(page, data)

                            res = _processar_paciente(page, ctx, pac, worklist, zip_root)
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

                try:
                    browser.close()
                except Exception:
                    pass

            i += BATCH_SIZE

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
                for fname in sorted(os.listdir(pasta_path)):
                    fpath = os.path.join(pasta_path, fname)
                    if os.path.isfile(fpath):
                        zf.write(fpath, arcname=os.path.join(res["pasta"], fname))

        buf.seek(0)
        zip_bytes = buf.getvalue()
        log(f"[OK] ZIP montado ({len(zip_bytes):,} bytes)")
        return zip_bytes, relatorio

    finally:
        shutil.rmtree(zip_root, ignore_errors=True)
