"""baixar_solicitacoes.py — baixa SÓ as solicitações dos pacientes Rede Unna de
um intervalo de datas. Fluxo HTTP puro por paciente (busca -> view_attachments ->
download), Playwright só p/ login + relatório do dia. Classifica cada anexo e
guarda apenas SOLICITACAO. Re-login automático se a sessão expirar. Anti-suspensão.

Uso:
  python baixar_solicitacoes.py 07/07/2026                 # 1 dia
  python baixar_solicitacoes.py 01/06/2026 11/07/2026      # intervalo (dias úteis)

Saída: OUT/<DDMMAAAA>/<cod>_<slug>/<solicitações> + manifest por dia + resumo.csv
Credenciais via env SMARTRIS_EMAIL / SMARTRIS_PASSWORD.
"""
import os, re, sys, json, time, ctypes, tempfile
from datetime import date, timedelta
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from extrator_pacientes_analitico import BASE_URL as BASE, get_credentials
from extrator_arquivos import _login_playwright, _get_relatorio_analitico, slug
from solicitacao_utils import tipo_anexo
from config import PLANOS

CONVENIOS = sorted({c for pl in PLANOS.values() for c in pl["convenios"]})
SEGMENTOS = sorted({s for pl in PLANOS.values() for s in pl["segmentos"]})
OUT = r"C:\Users\adoni\proradis_solicitacoes"
SKIP_IMG_BYTES = 1_500_000                      # imagem maior = raio-x, pula OCR
_IMG_EXT = (".bmp", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff")


def _keep_awake():
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    except Exception:
        pass


def _daterange(ini, fim):
    d0 = date(*map(int, ini.split("/")[::-1])); d1 = date(*map(int, fim.split("/")[::-1]))
    d = d0
    while d <= d1:
        if d.weekday() < 5:                     # pula fim de semana
            yield d.strftime("%d/%m/%Y")
        d += timedelta(days=1)


class Sessao:
    """Login Playwright (cookies + relatório do dia) + requests.Session p/ o resto."""
    def __init__(self, pw, email, password):
        self.pw, self.email, self.password = pw, email, password
        self._abrir()

    def _abrir(self):
        self.browser, self.ctx, self.page = _login_playwright(self.pw, self.email, self.password)
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest",
                                  "Referer": f"{BASE}/patients"})
        self._sync_cookies()

    def _sync_cookies(self):
        self.http.cookies.clear()
        self.http.cookies.update({c["name"]: c["value"] for c in self.ctx.cookies()})

    def relogin(self):
        _keep_awake()
        try:
            self.browser.close()
        except Exception:
            pass
        time.sleep(3); self._abrir()


def _sessao_expirada(resp) -> bool:
    return (resp.status_code != 200) or ("login/checklogin" in resp.text) or \
           ('name="password"' in resp.text and "search_patient" not in resp.url)


def _patient_id(html, cod):
    soup = BeautifulSoup(html, "lxml")
    for el in soup.select("[data-pat-id]"):
        if el.get("data-pat-id") == cod:
            m = re.search(r"load_patient_profile\('([^']+)'\)", str(el))
            if m:
                return m.group(1)
    m = re.search(r"load_patient_profile\('([^']+)'\)", html)
    return m.group(1) if m else None


def _baixar_solic_http(sess, cod, nome, pasta):
    """busca -> patient_id -> view_attachments -> download -> classifica. Salva só
    SOLICITACAO. Re-loga 1x se a sessão expirar."""
    for tentativa in (1, 2):
        try:
            r = sess.http.get(f"{BASE}/patients/search_patient_list/search_patient_list/",
                              params={"limit": 24, "input": nome}, timeout=30)
            if _sessao_expirada(r):
                raise RuntimeError("sessao_expirada")
            pid = _patient_id(r.text, cod)
            if not pid:
                return []                        # paciente não encontrado na busca
            r3 = sess.http.post(f"{BASE}/patients/view_attachments",
                               data={"patient_id": pid}, timeout=30)
            if _sessao_expirada(r3):
                raise RuntimeError("sessao_expirada")
            break
        except Exception as e:
            if tentativa == 1:
                print(f"     sessão/erro ({str(e)[:30]}) — re-login…", flush=True)
                sess.relogin(); continue
            return []
    itens = [{"id": d.get("data-id"), "fn": d.get("data-filename")}
             for d in BeautifulSoup(r3.text, "lxml").select(".attachment-item")]
    salvos = []
    for it in itens:
        try:
            body = sess.http.get(f"{BASE}/patients/download_attachment/{it['id']}/{cod}",
                                timeout=60).content
        except Exception:
            continue
        ext = os.path.splitext(it["fn"] or "")[1].lower() or ".bin"
        if ext in _IMG_EXT and len(body) > SKIP_IMG_BYTES:
            continue                              # raio-x: pula OCR
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp.write(body); tmp.close()
        try:
            tipo = tipo_anexo(tmp.name).get("tipo")
        except Exception:
            tipo = "?"
        if tipo == "SOLICITACAO":
            os.makedirs(pasta, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", it["fn"] or it["id"])
            with open(os.path.join(pasta, safe), "wb") as f:
                f.write(body)
            salvos.append(safe)
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
    return salvos


def baixar_dia(sess, data):
    tag = data.replace("/", ""); destino = os.path.join(OUT, tag)
    m = {"data": data, "pacientes": [], "n_solic": 0}
    df = None
    for tentativa in (1, 2):
        try:
            df = _get_relatorio_analitico(sess.page, CONVENIOS, SEGMENTOS, data); break
        except Exception as e:
            if tentativa == 1:
                sess.relogin(); continue
            print(f"[{data}] relatório falhou: {str(e)[:80]}"); return m
    if df is None or df.empty:
        print(f"[{data}] 0 pacientes."); return m
    cod_col = "Cód. Pac" if "Cód. Pac" in df.columns else df.columns[1]
    nome_col = "Paciente" if "Paciente" in df.columns else df.columns[2]
    pac = {}
    for _, r in df.iterrows():
        c = str(r[cod_col]).strip(); n = str(r[nome_col]).strip()
        if c not in pac or len(n) > len(pac[c]):
            pac[c] = n
    pacientes = sorted(pac.items())
    print(f"[{data}] {len(pacientes)} pacientes…", flush=True)
    for i, (cod, nome) in enumerate(pacientes, 1):
        _keep_awake()
        try:
            salvos = _baixar_solic_http(sess, cod, nome, os.path.join(destino, f"{cod}_{slug(nome)}"))
        except Exception as e:
            salvos = []; print(f"  [{i}] {nome[:26]} ERRO: {str(e)[:40]}", flush=True)
        m["pacientes"].append({"cod": cod, "nome": nome, "solicitacoes": salvos})
        m["n_solic"] += len(salvos)
        print(f"  [{i}/{len(pacientes)}] {nome[:26]:28} {len(salvos)} solic.", flush=True)
    if m["pacientes"]:
        os.makedirs(destino, exist_ok=True)
        json.dump(m, open(os.path.join(destino, "manifest.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    print(f"[{data}] => {m['n_solic']} solicitações.", flush=True)
    return m


def main():
    if len(sys.argv) < 2:
        print("uso: python baixar_solicitacoes.py DD/MM/AAAA [DD/MM/AAAA]"); return
    ini = sys.argv[1]; fim = sys.argv[2] if len(sys.argv) > 2 else ini
    os.makedirs(OUT, exist_ok=True); _keep_awake()
    email, password = get_credentials()
    print(f"Convênios={len(CONVENIOS)} Segmentos={len(SEGMENTOS)} | de {ini} a {fim}", flush=True)
    t0 = time.time(); total = 0; resumo = []
    with sync_playwright() as pw:
        sess = Sessao(pw, email, password)
        try:
            for data in _daterange(ini, fim):
                mpath = os.path.join(OUT, data.replace("/", ""), "manifest.json")
                if os.path.exists(mpath):                    # resume: dia já feito
                    prev = json.load(open(mpath, encoding="utf-8"))
                    n = prev.get("n_solic", 0)
                    print(f"[{data}] já feito ({n} solic) — pulando.", flush=True)
                    total += n; resumo.append((data, n, len(prev.get("pacientes", [])))); continue
                mm = baixar_dia(sess, data)
                total += mm["n_solic"]; resumo.append((data, mm["n_solic"], len(mm["pacientes"])))
        finally:
            sess.browser.close()
    with open(os.path.join(OUT, "resumo.csv"), "w", encoding="utf-8") as f:
        f.write("data,solicitacoes,pacientes\n")
        for d, sname, pnum in resumo:
            f.write(f"{d},{sname},{pnum}\n")
    print(f"\n[OK] {total} solicitações em {(time.time()-t0)/60:.1f} min. Pasta: {OUT}", flush=True)


if __name__ == "__main__":
    main()
