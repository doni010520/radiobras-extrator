"""
esteira.py — Esteira PARALELA das 2 primeiras etapas (descoberta + download),
reusável (chamável de uma rota/job). NÃO anexa.

  DESCOBERTA: N sessões OdontoPrev independentes; cada uma carrega a tabela do
     dia e abre SÓ o seu fatiado (gto % N == wid) -> robusto a ordenação.
     Cada pendente cai na fila.
  DOWNLOAD: M workers PRORADIS (sessão compartilhada via storage_state); cada
     worker pega da fila, baixa laudo+imagens (real), e ao terminar pega o
     próximo (vaga liberou -> entra outro).

rodar_esteira(data, m_download, n_desc, log) -> dict (resumo + medições).
"""
import os
import queue
import tempfile
import threading
import time

from playwright.sync_api import sync_playwright

from config import CONVENIOS, SEGMENTOS
from extrator_pacientes_analitico import BASE_URL as BASE, get_credentials
from extrator_arquivos import (
    _login_playwright, _get_relatorio_analitico,
    listar_worklist_por_pacientes, _processar_paciente,
)
from extrator_odontoprev import (
    login_odonto, get_credentials_odonto, abrir_consultar_gtos,
    consultar_periodo, listar_gtos, abrir_gto, _anexos_nomes, _anexos_count,
    normaliza_nome,
)
from fechar_dia import _prefixo_casa, _ja_anexado_por_nos
from extrair_anexos_dia import anexos_do_paciente
import requests

try:
    import psutil
    _PROC = psutil.Process(os.getpid())
except Exception:
    psutil = None
    _PROC = None

_GEM_PROMPT = ("É uma solicitação/requisição de exames odontológicos? Se sim, responda em "
               "JSON com {solicitacao:true, tipo:'digitada'|'manuscrita', legivel:bool, "
               "exames:[...]}. Se não, {solicitacao:false}. Responda só o JSON.")


def _mem_mb():
    if not _PROC:
        return -1
    try:
        tot = _PROC.memory_info().rss
        for ch in _PROC.children(recursive=True):
            try:
                tot += ch.memory_info().rss
            except Exception:
                pass
        return tot / 1e6
    except Exception:
        return -1


def _build_by_norm(df):
    cod_col = "Cód. Pac" if "Cód. Pac" in df.columns else df.columns[1]
    ped_col = "Pedido" if "Pedido" in df.columns else df.columns[6]
    nome_col = "Paciente" if "Paciente" in df.columns else df.columns[2]
    by = {}
    for _, r in df.iterrows():
        nm = str(r[nome_col]).strip()
        lst = by.setdefault(normaliza_nome(nm), [])
        pac = next((p for p in lst if p["cod_pac"] == str(r[cod_col]).strip()), None)
        if not pac:
            pac = {"cod_pac": str(r[cod_col]).strip(), "nome": nm, "accessions": []}
            lst.append(pac)
        a = str(r[ped_col]).strip()
        if a and a not in pac["accessions"]:
            pac["accessions"].append(a)
        if len(nm) > len(pac["nome"]):
            pac["nome"] = nm
    return by


def _ler_solic_gemini(gem, pg, ctx, pac):
    """Baixa anexos do prontuário e manda cada um pro Gemini 2.5 Flash (substitui
    o OCR Tesseract). I/O-bound -> libera CPU. Retry 3x no 503. Devolve (n_anexos,
    n_lidas_ok)."""
    from google.genai import types
    try:
        lista = anexos_do_paciente(pg, pac["nome"], pac["cod_pac"])
    except Exception:
        return 0, 0
    cj = {ck["name"]: ck["value"] for ck in ctx.cookies()}
    sess = requests.Session(); sess.cookies.update(cj)
    sess.headers.update({"User-Agent": "Mozilla/5.0", "Referer": f"{BASE}/patients"})
    lidas = 0
    for it in lista:
        if lidas >= 3:   # teto p/ não estourar rate-limit da free tier no teste
            break
        ext = it["filename"].lower().rsplit(".", 1)[-1] if "." in it["filename"] else ""
        mime = {"pdf": "application/pdf", "png": "image/png",
                "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext)
        if not mime:
            continue
        try:
            blob = sess.get(it["url"], timeout=60).content
        except Exception:
            continue
        for tent in range(3):  # 503 do Gemini é transitório
            try:
                gem.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[types.Part.from_bytes(data=blob, mime_type=mime), _GEM_PROMPT])
                lidas += 1
                break
            except Exception:
                time.sleep(1.2 * (tent + 1))
    return len(lista), lidas


def _baixa_um(pg, ctx, by_norm, g, tmp, data, gem=None):
    """Match (igual ao _proc do fechar_dia) + download real. NÃO anexa.
    Se gem != None, lê as solicitações via Gemini (sem Tesseract)."""
    t0 = time.monotonic()
    nn = g["nome_norm"]
    cands = by_norm.get(nn, [])
    if not cands:
        vistos, pref = set(), []
        for key, lst in by_norm.items():
            if _prefixo_casa(key, nn):
                for p in lst:
                    if p["cod_pac"] not in vistos:
                        vistos.add(p["cod_pac"]); pref.append(p)
        cands = pref
    if len(cands) > 1:
        return {"gto": g["gto"], "nome": g["nome"], "status": "AMBIGUO", "dt": time.monotonic() - t0}
    if cands:
        pac = cands[0]
        wl = listar_worklist_por_pacientes(pg, data, [pac["nome"]])
    else:
        wl = listar_worklist_por_pacientes(pg, data, [g["nome"]])
        accs = sorted({w["accession"] for w in wl if w.get("accession")})
        toks = g["nome"].split()
        while not accs and len(toks) > 2:
            toks = toks[:-1]
            wl = listar_worklist_por_pacientes(pg, data, [" ".join(toks)])
            accs = sorted({w["accession"] for w in wl if w.get("accession")})
        if not accs:
            return {"gto": g["gto"], "nome": g["nome"], "status": "SEM_MATCH", "dt": time.monotonic() - t0}
        pac = {"nome": g["nome"], "cod_pac": "WL" + accs[0], "accessions": accs}
    res = _processar_paciente(pg, ctx, pac, wl, tmp, data)
    pasta = os.path.join(tmp, res["pasta"])
    nf = len(os.listdir(pasta)) if os.path.isdir(pasta) else 0
    n_anx = n_lidas = 0
    if gem is not None:
        n_anx, n_lidas = _ler_solic_gemini(gem, pg, ctx, pac)
    return {"gto": g["gto"], "nome": pac["nome"], "status": "BAIXADO",
            "arquivos": nf, "imgs": res.get("imagens", {}).get("qtd", 0),
            "anexos": n_anx, "lidas_gemini": n_lidas,
            "dt": time.monotonic() - t0}


def rodar_esteira(data, m_download=4, n_desc=2, log=None, gemini_key=None):
    """Roda a esteira paralela e devolve um resumo com medições.
    gemini_key: se setada, lê solicitações via Gemini 2.5 Flash (sem Tesseract)."""
    if log is None:
        log = lambda m: print(m, flush=True)
    t_glob = time.monotonic()

    def _t(m):
        log(f"[{time.monotonic() - t_glob:6.0f}s] {m}")

    gem = None
    if gemini_key:
        try:
            from google import genai
            gem = genai.Client(api_key=gemini_key)
            _t("Gemini 2.5 Flash ATIVO (lê solicitações; Tesseract fora)")
        except Exception as e:
            _t(f"Gemini indisponível ({str(e)[:80]}) — segue sem leitura")

    fila = queue.Queue()
    stop_desc = threading.Event()
    _lock = threading.Lock()
    resultados = []
    ativos = {"n": 0, "pico": 0}
    n_pendentes = {"n": 0}

    def descobridor_worker(wid):
        user, pwd = get_credentials_odonto()
        with sync_playwright() as pw:
            br, ctx, pg = login_odonto(pw, user, pwd)
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)
            try:
                abrir_consultar_gtos(pg); consultar_periodo(pg, data)
                gtos = listar_gtos(pg)
                do_dia = [g for g in gtos if g.get("liberacao") == data] or gtos
                alvos = [g for g in do_dia if "REPASSE" in g["status"].upper()]
                meus = [g for g in alvos if g["gto"].isdigit() and int(g["gto"]) % n_desc == wid]
                _t(f"[DESC{wid}] {len(alvos)} alvo(s) no dia | eu cuido de {len(meus)}")
                for g in meus:
                    try:
                        gp = abrir_gto(pg, g["gto"], _refrescar=None)
                        gp.wait_for_timeout(800)
                        nomes = _anexos_nomes(gp); cnt = _anexos_count(gp)
                        try:
                            gp.close()
                        except Exception:
                            pass
                    except Exception:
                        g["nome_norm"] = normaliza_nome(g["nome"])
                        with _lock:
                            n_pendentes["n"] += 1
                        fila.put(g)
                        _t(f"[DESC{wid}] GTO {g['gto']} não abriu -> manda processar")
                        continue
                    if _ja_anexado_por_nos(nomes) or cnt >= 2:
                        _t(f"[DESC{wid}] GTO {g['gto']} {g['nome']}: já completa -> pula")
                        continue
                    g["nome_norm"] = normaliza_nome(g["nome"])
                    with _lock:
                        n_pendentes["n"] += 1
                    _t(f"[DESC{wid}] >>> PENDENTE {g['gto']} {g['nome']} -> FILA (fila={fila.qsize()})")
                    fila.put(g)
            finally:
                br.close()
        _t(f"[DESC{wid}] terminou minha parte")

    def download_worker(wid, state, by_norm, tmp, gem):
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = br.new_context(storage_state=state, locale="pt-BR", timezone_id="America/Sao_Paulo")
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)
            pg = ctx.new_page()
            # pousar no domínio do PRORADIS: a worklist faz fetch('/ris/...') relativo
            pg.goto(f"{BASE}/admin_reports", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(800)
            while True:
                try:
                    g = fila.get(timeout=2)
                except queue.Empty:
                    if stop_desc.is_set() and fila.empty():
                        break
                    continue
                with _lock:
                    ativos["n"] += 1
                    ativos["pico"] = max(ativos["pico"], ativos["n"])
                    em = _mem_mb()
                _t(f"[W{wid}] inicia {g['gto']} {g['nome']} (CONCORRENTES={ativos['n']}, mem={em:.0f}MB)")
                try:
                    r = _baixa_um(pg, ctx, by_norm, g, tmp, data, gem)
                except Exception as e:
                    r = {"gto": g["gto"], "nome": g["nome"], "status": "ERRO", "erro": str(e)[:120], "dt": 0}
                with _lock:
                    ativos["n"] -= 1
                    resultados.append(r)
                _t(f"[W{wid}] FIM {g['gto']} -> {r['status']} ({r.get('arquivos', 0)} arq, {r.get('dt', 0):.0f}s)")
            try:
                br.close()
            except Exception:
                pass

    # 1) PRORADIS: relatório analítico (by_norm) + storage_state
    _t(f"=== ESTEIRA {data} | descoberta={n_desc} | download={m_download} ===")
    email, password = get_credentials()
    with sync_playwright() as pw0:
        br0, ctx0, pg0 = _login_playwright(pw0, email, password)
        ctx0.set_default_timeout(45000); ctx0.set_default_navigation_timeout(60000)
        df = _get_relatorio_analitico(pg0, CONVENIOS, SEGMENTOS, data)
        by_norm = _build_by_norm(df)
        state = ctx0.storage_state()
        br0.close()
    _t(f"PRORADIS ok | by_norm={len(by_norm)} pacientes | cookies={len(state.get('cookies', []))}")
    tmp = tempfile.mkdtemp(prefix="_esteira_")

    # 2) N descobridores + M downloads em paralelo
    tds = [threading.Thread(target=descobridor_worker, args=(i,), daemon=True) for i in range(n_desc)]
    tws = [threading.Thread(target=download_worker, args=(i, state, by_norm, tmp, gem), daemon=True)
           for i in range(1, m_download + 1)]
    t_ini = time.monotonic()
    for t in tds:
        t.start()
    for t in tws:
        t.start()
    for t in tds:
        t.join()
    t_desc = time.monotonic() - t_ini
    stop_desc.set()
    _t(f"[DESC] descoberta COMPLETA em {t_desc:.0f}s ({n_desc} sessão/oes)")
    for t in tws:
        t.join()
    total = time.monotonic() - t_ini

    baixados = [r for r in resultados if r["status"] == "BAIXADO"]
    ts = [r["dt"] for r in baixados]
    resumo = {
        "data": data, "n_desc": n_desc, "m_download": m_download,
        "pendentes": n_pendentes["n"], "baixados": len(baixados),
        "outros": len(resultados) - len(baixados),
        "pico_download": ativos["pico"],
        "dl_min": round(min(ts)) if ts else 0,
        "dl_med": round(sum(ts) / len(ts)) if ts else 0,
        "dl_max": round(max(ts)) if ts else 0,
        "tempo_descoberta": round(t_desc),
        "tempo_total": round(total),
        "gemini": bool(gem),
        "anexos_lidos": sum(r.get("lidas_gemini", 0) for r in baixados),
        "resultados": resultados,
    }
    _t(f"RESUMO: {resumo['baixados']}/{resumo['pendentes']} baixados | "
       f"pico={resumo['pico_download']} | descoberta={resumo['tempo_descoberta']}s | "
       f"TOTAL={resumo['tempo_total']}s")
    return resumo
