"""
esteira.py — Pipeline PARALELO de 3 estágios (descoberta -> download -> leitura).
NÃO anexa. Cada estágio tem fila + pool próprios, então rodam sobrepostos.

  DESCOBERTA  (N sessões OdontoPrev): abre cada GTO alvo, conta anexos, pendente
              -> fila_pend.
  DOWNLOAD    (M sessões PRORADIS, sessão compartilhada): baixa laudo+imagens
              (rápido, ~13s) e ENTREGA pra fila_leit (não fica preso na leitura).
  LEITURA     (K sessões PRORADIS + Gemini 2.5 Flash): baixa anexos do prontuário
              e lê as solicitações via Gemini (I/O-bound; substitui o Tesseract).

A separação da leitura num pool próprio é o ponto: o download não trava na
leitura, e a leitura escala sozinha (limitada pela cota do Gemini, não pela CPU).

rodar_esteira(data, m_download, n_desc, k_leitura, log, gemini_key) -> resumo.
"""
import os
import queue
import tempfile
import threading
import time

import requests
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

try:
    import psutil
    _PROC = psutil.Process(os.getpid())
except Exception:
    psutil = None
    _PROC = None

_GEM_PROMPT = ("É uma solicitação/requisição de exames odontológicos? Se sim, responda em "
               "JSON {solicitacao:true, tipo:'digitada'|'manuscrita', legivel:bool, exames:[...]}. "
               "Se não, {solicitacao:false}. Responda só o JSON.")
_MAX_LEITURAS = 5  # teto de anexos lidos por paciente (mesmo no tier pago)


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


def _baixa_um(pg, ctx, by_norm, g, tmp, data):
    """ESTÁGIO 2 (download only): match + baixa laudo+imagens. Devolve item com
    _pac embutido (p/ o estágio de leitura). NÃO lê solicitação aqui."""
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
        return {"gto": g["gto"], "nome": g["nome"], "status": "AMBIGUO", "dt_dl": time.monotonic() - t0}
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
            return {"gto": g["gto"], "nome": g["nome"], "status": "SEM_MATCH", "dt_dl": time.monotonic() - t0}
        pac = {"nome": g["nome"], "cod_pac": "WL" + accs[0], "accessions": accs}
    res = _processar_paciente(pg, ctx, pac, wl, tmp, data)
    pasta = os.path.join(tmp, res["pasta"])
    nf = len(os.listdir(pasta)) if os.path.isdir(pasta) else 0
    return {"gto": g["gto"], "nome": pac["nome"], "status": "BAIXADO",
            "arquivos": nf, "imgs": res.get("imagens", {}).get("qtd", 0),
            "_pac": pac, "dt_dl": time.monotonic() - t0}


def _ler_solic(gem, pg, ctx, pac):
    """ESTÁGIO 3 (leitura): baixa anexos do prontuário e lê cada um no Gemini 2.5
    Flash (I/O-bound; sem Tesseract). Retry 3x no 503. Devolve (n_anexos, n_lidas)."""
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
        if lidas >= _MAX_LEITURAS:
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
                time.sleep(1.0 * (tent + 1))
    return len(lista), lidas


def rodar_esteira(data, m_download=3, n_desc=3, k_leitura=4, log=None, gemini_key=None):
    """Pipeline de 3 estágios. gemini_key liga o estágio de leitura (sem ela, só
    descoberta+download)."""
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
            _t(f"Gemini 2.5 Flash ATIVO | pool de leitura K={k_leitura} (Tesseract fora)")
        except Exception as e:
            _t(f"Gemini indisponível ({str(e)[:80]}) — roda sem leitura")

    fila_pend = queue.Queue()
    fila_leit = queue.Queue()
    stop_desc = threading.Event()
    stop_dl = threading.Event()
    _lock = threading.Lock()
    resultados = []
    n_pend = {"n": 0}
    ativos_dl = {"n": 0, "pico": 0}
    ativos_le = {"n": 0, "pico": 0}

    # ---- ESTÁGIO 1: descoberta (OdontoPrev) ----
    def descobridor(wid):
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
                _t(f"[DESC{wid}] {len(alvos)} alvo(s) | eu cuido de {len(meus)}")
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
                            n_pend["n"] += 1
                        fila_pend.put(g)
                        continue
                    if _ja_anexado_por_nos(nomes) or cnt >= 2:
                        _t(f"[DESC{wid}] GTO {g['gto']}: já completa -> pula")
                        continue
                    g["nome_norm"] = normaliza_nome(g["nome"])
                    with _lock:
                        n_pend["n"] += 1
                    _t(f"[DESC{wid}] >>> PENDENTE {g['gto']} {g['nome']} -> fila_pend")
                    fila_pend.put(g)
            finally:
                br.close()

    # ---- ESTÁGIO 2: download (PRORADIS) ----
    def baixador(wid, state, by_norm, tmp):
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = br.new_context(storage_state=state, locale="pt-BR", timezone_id="America/Sao_Paulo")
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)
            pg = ctx.new_page()
            pg.goto(f"{BASE}/admin_reports", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(800)
            while True:
                try:
                    g = fila_pend.get(timeout=2)
                except queue.Empty:
                    if stop_desc.is_set() and fila_pend.empty():
                        break
                    continue
                with _lock:
                    ativos_dl["n"] += 1; ativos_dl["pico"] = max(ativos_dl["pico"], ativos_dl["n"])
                try:
                    r = _baixa_um(pg, ctx, by_norm, g, tmp, data)
                except Exception as e:
                    r = {"gto": g["gto"], "nome": g["nome"], "status": "ERRO", "erro": str(e)[:120]}
                with _lock:
                    ativos_dl["n"] -= 1
                _t(f"[DL{wid}] {g['gto']} -> {r['status']} ({r.get('dt_dl', 0):.0f}s)")
                if gem is not None and r.get("status") == "BAIXADO" and r.get("_pac"):
                    fila_leit.put(r)         # entrega pro estágio de leitura
                else:
                    with _lock:
                        resultados.append(r)
            try:
                br.close()
            except Exception:
                pass

    # ---- ESTÁGIO 3: leitura (PRORADIS + Gemini) ----
    def leitor(wid, state):
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = br.new_context(storage_state=state, locale="pt-BR", timezone_id="America/Sao_Paulo")
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)
            pg = ctx.new_page()
            pg.goto(f"{BASE}/admin_reports", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(800)
            while True:
                try:
                    item = fila_leit.get(timeout=2)
                except queue.Empty:
                    if stop_dl.is_set() and fila_leit.empty():
                        break
                    continue
                with _lock:
                    ativos_le["n"] += 1; ativos_le["pico"] = max(ativos_le["pico"], ativos_le["n"])
                    em = _mem_mb()
                t0 = time.monotonic()
                try:
                    n_anx, n_lidas = _ler_solic(gem, pg, ctx, item["_pac"])
                except Exception as e:
                    n_anx, n_lidas = 0, 0
                    item["leitura_erro"] = str(e)[:100]
                item["anexos"] = n_anx; item["lidas_gemini"] = n_lidas
                item["dt_leitura"] = time.monotonic() - t0
                with _lock:
                    ativos_le["n"] -= 1
                    resultados.append(item)
                _t(f"[LE{wid}] {item['gto']} -> {n_lidas}/{n_anx} lidas "
                   f"({item['dt_leitura']:.0f}s, leitores={ativos_le['n'] + 1}, mem={em:.0f}MB)")
            try:
                br.close()
            except Exception:
                pass

    # ---- 1) PRORADIS: relatório analítico (by_norm) + storage_state ----
    _t(f"=== PIPELINE {data} | desc={n_desc} dl={m_download} leit={k_leitura if gem else 0} ===")
    email, password = get_credentials()
    with sync_playwright() as pw0:
        br0, ctx0, pg0 = _login_playwright(pw0, email, password)
        ctx0.set_default_timeout(45000); ctx0.set_default_navigation_timeout(60000)
        df = _get_relatorio_analitico(pg0, CONVENIOS, SEGMENTOS, data)
        by_norm = _build_by_norm(df)
        state = ctx0.storage_state()
        br0.close()
    _t(f"PRORADIS ok | by_norm={len(by_norm)} | cookies={len(state.get('cookies', []))}")
    tmp = tempfile.mkdtemp(prefix="_esteira_")

    # ---- 2) lança os 3 pools ----
    tds = [threading.Thread(target=descobridor, args=(i,), daemon=True) for i in range(n_desc)]
    tws = [threading.Thread(target=baixador, args=(i, state, by_norm, tmp), daemon=True)
           for i in range(1, m_download + 1)]
    tls = ([threading.Thread(target=leitor, args=(i, state), daemon=True)
            for i in range(1, k_leitura + 1)] if gem else [])
    t_ini = time.monotonic()
    for t in tds + tws + tls:
        t.start()
    for t in tds:
        t.join()
    t_desc = time.monotonic() - t_ini
    stop_desc.set()
    for t in tws:
        t.join()
    t_dl = time.monotonic() - t_ini
    stop_dl.set()
    for t in tls:
        t.join()
    total = time.monotonic() - t_ini

    baixados = [r for r in resultados if r["status"] == "BAIXADO"]
    resumo = {
        "data": data, "n_desc": n_desc, "m_download": m_download,
        "k_leitura": k_leitura if gem else 0, "gemini": bool(gem),
        "pendentes": n_pend["n"], "baixados": len(baixados),
        "outros": len(resultados) - len(baixados),
        "pico_download": ativos_dl["pico"], "pico_leitura": ativos_le["pico"],
        "anexos_lidos": sum(r.get("lidas_gemini", 0) for r in baixados),
        "tempo_descoberta": round(t_desc), "tempo_ate_download": round(t_dl),
        "tempo_total": round(total), "resultados": resultados,
    }
    _t(f"RESUMO: {resumo['baixados']}/{resumo['pendentes']} baixados | "
       f"pico dl={resumo['pico_download']} leit={resumo['pico_leitura']} | "
       f"desc={resumo['tempo_descoberta']}s download={resumo['tempo_ate_download']}s "
       f"TOTAL={resumo['tempo_total']}s")
    return resumo
