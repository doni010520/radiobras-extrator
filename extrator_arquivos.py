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
from playwright.sync_api import sync_playwright

from extrator_pacientes_analitico import (
    BASE_URL as BASE,
    discover_tokens_and_cookies,
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
    try:
        page.wait_for_url("**/calendar*", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(2500)
    if "/login" in page.url or "checklogin" in page.url:
        browser.close()
        raise RuntimeError("Falha no login — verificar credenciais.")
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

def listar_worklist_dia(page, data: str) -> list:
    """
    POST /ris/reports_list/get_list via JS fetch (mantem cookies de sessao).
    data: 'DD/MM/YYYY'
    Retorna: [{accession, nome, rows_html: [str]}, ...]
    """
    dt_inicio = f"{data} 00:00:00"
    dt_fim = f"{data} 23:59:59"

    raw_html = page.evaluate(
        """async ([inicio, fim]) => {
            const body = new URLSearchParams({
                'busca-por': 'name',
                'filtro[nome]': '',
                'filtro[exames]': 'todos',
                'filtro[tipo_data]': 'study_datetime',
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
        }""",
        [dt_inicio, dt_fim],
    )

    soup = BeautifulSoup(raw_html, "lxml")
    by_acc: dict = {}

    for tr in soup.find_all("tr"):
        h = str(tr)
        # Accession: 8 digitos comecando com 40 (ex.: 40322253)
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
        by_acc[acc]["rows_html"].append(h)

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

def baixar_imagens(page, ctx, study_id: str, schedule_id: str, out_dir: str) -> dict:
    """
    Porta _entrega.py: intercepta viewer/u/image, abre popup reports_doc,
    salva so as imagens do grupo DOCUMENTACAO COMPLETA.
    Retorna: {qtd, arquivos, pendencias}
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
            return {"qtd": 0, "arquivos": [], "pendencias": pendencias}

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

    # Salvar imagens entregaveis com dedup por hash
    seen: set = set()
    n = 0
    arquivos: list = []
    for tail, body in captured:
        if tail in deliver_tails:
            h = hashlib.md5(body).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            n += 1
            fname = f"ENTREGA_{n}.jpg"
            with open(os.path.join(out_dir, fname), "wb") as f:
                f.write(body)
            arquivos.append(fname)

    if n == 0 and deliver_tails:
        pendencias.append("imagens nao prontas (grupo encontrado mas sem JPEG capturado)")

    return {"qtd": n, "arquivos": arquivos, "pendencias": pendencias}


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

    # --- Ceph via render Playwright (pagina isolada) ---
    for tok_info in tokens_list:
        exame = tok_info["exame"] or "EXAME"
        for tok in tok_info["ceph"]:
            ceph_page = ctx.new_page()
            try:
                url = f"{BASE}/report_ceph/print_preview/{tok}"
                ceph_page.goto(url, wait_until="networkidle")
                ceph_page.wait_for_timeout(3500)
                ceph_page.evaluate(
                    "() => { document.querySelectorAll"
                    "('#bg-loading,.loading,#loading').forEach(e => e.remove()); }"
                )
                fname = f"LAUDO_{exame}_CEPH.pdf"
                fpath = os.path.join(out_dir, fname)
                ceph_page.pdf(path=fpath, format="A4", print_background=True)
                size = os.path.getsize(fpath)
                if size < 10_000:  # ≈857B = pagina de erro renderizada
                    os.remove(fpath)
                    resultados.append(
                        {"exame": exame, "arquivo": None, "bytes": size, "status": "NAO_PRONTO"}
                    )
                else:
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
        for tok in tok_info["pan"]:
            try:
                r = sess.get(f"{BASE}/report/pdf?studies={tok}", timeout=60)
                if r.content[:4] == b"%PDF":
                    fname = f"LAUDO_{exame}_OFICIAL.pdf"
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


# ── Processamento por paciente ────────────────────────────────────────────────

def _processar_paciente(page, ctx, pac: dict, worklist: list, zip_root: str) -> dict:
    """Processa um paciente dentro de uma sessao Playwright ativa."""
    nome = pac["nome"]
    acc = pac["accession"]
    pasta_nome = f"{slug(nome)}_{acc}"
    out_dir = os.path.join(zip_root, pasta_nome)
    os.makedirs(out_dir, exist_ok=True)

    resultado = {
        "nome": nome,
        "accession": acc,
        "pasta": pasta_nome,
        "status": "PENDENTE",
        "imagens": {"qtd": 0, "arquivos": []},
        "laudos": [],
        "pendencias": [],
    }

    # Idempotencia: pular se pasta ja completa e FORCE=False
    if not FORCE and os.path.isdir(out_dir):
        existing = os.listdir(out_dir)
        imgs = [f for f in existing if f.startswith("ENTREGA_") and f.endswith(".jpg")]
        laudos_f = [f for f in existing if f.startswith("LAUDO_")]
        if imgs and laudos_f:
            resultado["status"] = "OK"
            resultado["imagens"] = {"qtd": len(imgs), "arquivos": sorted(imgs)}
            resultado["laudos"] = [
                {
                    "exame": re.sub(r"^LAUDO_|_(OFICIAL|CEPH)\.pdf$", "", f),
                    "arquivo": f,
                    "bytes": os.path.getsize(os.path.join(out_dir, f)),
                    "status": "OK",
                }
                for f in sorted(laudos_f)
            ]
            return resultado

    # Localizar paciente na worklist pelo accession
    wl_pac = next((w for w in worklist if w["accession"] == acc), None)
    if not wl_pac:
        resultado["status"] = "ERRO"
        resultado["pendencias"].append("nao localizado na worklist")
        return resultado

    # Extrair tokens de todas as linhas do paciente
    tokens_list = [extrair_tokens(h) for h in wl_pac["rows_html"]]
    doc = next((t["doc"] for t in tokens_list if t["doc"]), None)

    # Download de imagens
    if doc:
        try:
            img_res = baixar_imagens(page, ctx, doc["study_id"], doc["schedule_id"], out_dir)
            resultado["imagens"] = {"qtd": img_res["qtd"], "arquivos": img_res["arquivos"]}
            resultado["pendencias"].extend(img_res.get("pendencias", []))
        except Exception as e:
            resultado["pendencias"].append(f"erro imagens: {e}")
    else:
        resultado["pendencias"].append("sem token reports_doc na worklist")

    # Download de laudos
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

    # Status final
    tem_imagem = resultado["imagens"]["qtd"] > 0
    todos_ok = all(l["status"] == "OK" for l in resultado["laudos"]) if resultado["laudos"] else True
    if tem_imagem and todos_ok and not resultado["pendencias"]:
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
        lines.append(f"{tag} {pac['nome']} ({pac['accession']})")
        qtd = pac["imagens"]["qtd"]
        laudos_parts = []
        for lau in pac["laudos"]:
            tipo = "(ceph)" if "CEPH" in (lau.get("arquivo") or "") else ""
            laudos_parts.append(f"{lau['exame']}{tipo} {lau['status']}")
        detalhe = " | ".join([f"imagens={qtd}"] + laudos_parts)
        lines.append(f"           {detalhe}")
        for pend in pac.get("pendencias", []):
            lines.append(f"           !! {pend}")
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

    # A: lista de pacientes via relatorio analitico (reutiliza extrator_pacientes_analitico)
    log("[A] Relatorio analitico REDE UNNA...")
    conv_map, seg_map, cookies = discover_tokens_and_cookies(email, password)
    ins_toks = resolve_tokens(convenios, conv_map, "convenio")
    seg_toks = resolve_tokens(segmentos, seg_map, "segmento")
    html_rel = post_relatorio(cookies, ins_toks, seg_toks, data, data)
    df, _, _ = parse_html_to_df(html_rel)

    if df.empty:
        raise ValueError(f"Nenhum paciente REDE UNNA em {data}.")

    pedido_col = "Pedido" if "Pedido" in df.columns else df.columns[6]
    nome_col = "Paciente" if "Paciente" in df.columns else df.columns[2]

    seen_acc: set = set()
    pacientes: list = []
    for _, row in df.iterrows():
        acc = str(row[pedido_col]).strip()
        nome = str(row[nome_col]).strip()
        if acc and acc not in seen_acc:
            seen_acc.add(acc)
            pacientes.append({"accession": acc, "nome": nome})

    pacientes.sort(key=lambda x: x["accession"])
    log(f"   {len(pacientes)} pacientes unicos.")

    # B: processar em lotes
    job_ts = datetime.now().strftime("%Y%m%d%H%M%S")
    zip_root = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), f"_tmp_{job_ts}"
    )
    os.makedirs(zip_root, exist_ok=True)

    resultados: list = []
    i = 0

    try:
        while i < len(pacientes):
            lote = pacientes[i: i + BATCH_SIZE]
            log(f"\n[LOTE {i // BATCH_SIZE + 1}] {i + 1}–{i + len(lote)} / {len(pacientes)}")

            with sync_playwright() as pw:
                browser = ctx = page = None
                worklist = []

                try:
                    browser, ctx, page = _login_playwright(pw, email, password)
                    worklist = listar_worklist_dia(page, data)
                    log(f"   Worklist: {len(worklist)} accessions")
                except Exception as e:
                    log(f"   Falha login/worklist: {e}")
                    for pac in lote:
                        resultados.append({
                            "nome": pac["nome"],
                            "accession": pac["accession"],
                            "pasta": f"{slug(pac['nome'])}_{pac['accession']}",
                            "status": "ERRO",
                            "imagens": {"qtd": 0, "arquivos": []},
                            "laudos": [],
                            "pendencias": [f"falha login/worklist: {e}"],
                        })
                    i += BATCH_SIZE
                    continue

                for pac in lote:
                    log(f"  -> {pac['nome']} ({pac['accession']})")
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
                                    "accession": pac["accession"],
                                    "pasta": f"{slug(pac['nome'])}_{pac['accession']}",
                                    "status": "ERRO",
                                    "imagens": {"qtd": 0, "arquivos": []},
                                    "laudos": [],
                                    "pendencias": [
                                        f"erro apos {MAX_RETRIES} tentativas: {e}"
                                    ],
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
