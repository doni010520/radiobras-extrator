"""Rede de seguranca por replay (Fase 1 da reorganizacao, 09/10/2026).

Grava uma rodada DRY real (tudo que vem de fora) num CASSETE e a repete offline,
de forma deterministica, para provar que uma reorganizacao nao muda decisao de
faturamento. Cassete tem documento medico: fica FORA do git (RB_REPLAY_DIR)."""
import hashlib
import json
import os
import threading

PASTA_PADRAO = os.environ.get("RB_REPLAY_DIR", r"C:\Users\adoni\rb-replay\cassetes")


class ReplayFaltando(Exception):
    """O codigo pediu algo que a gravacao nao tem: a reorganizacao mudou uma
    chamada externa (ou o cassete esta incompleto). Nunca inventar resposta."""


class Cassete:
    def __init__(self, pasta: str):
        self.pasta = pasta
        self._lock = threading.Lock()
        self._arq = os.path.join(pasta, "chamadas.json")
        self._dados, self.meta = {}, {}
        if os.path.exists(self._arq):
            with open(self._arq, encoding="utf-8") as f:
                j = json.load(f)
            self._dados, self.meta = j.get("chamadas", {}), j.get("meta", {})

    def gravar(self, costura: str, chave: str, valor) -> None:
        with self._lock:
            self._dados.setdefault(costura, {})[str(chave)] = valor

    def tocar(self, costura: str, chave: str):
        try:
            return self._dados[costura][str(chave)]
        except KeyError:
            raise ReplayFaltando(f"{costura}: chave nao gravada {str(chave)[:160]!r}")

    def gravar_blob(self, dados: bytes) -> str:
        sha = hashlib.sha256(dados or b"").hexdigest()
        d = os.path.join(self.pasta, "blobs")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, sha)
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(dados or b"")
        return sha

    def ler_blob(self, sha: str) -> bytes:
        with open(os.path.join(self.pasta, "blobs", sha), "rb") as f:
            return f.read()

    def salvar(self) -> None:
        os.makedirs(self.pasta, exist_ok=True)
        with self._lock, open(self._arq, "w", encoding="utf-8") as f:
            json.dump({"meta": self.meta, "chamadas": self._dados}, f,
                      ensure_ascii=False, indent=1, default=str)


def _forma(x, h):
    """Alimenta o hash com a FORMA do conteudo enviado ao Gemini: texto como texto,
    bytes pelo sha. Objetos do SDK (Part) lidos por atributo, sem importar o SDK."""
    if x is None:
        h.update(b"N")
    elif isinstance(x, str):
        h.update(b"S" + x.encode("utf-8"))
    elif isinstance(x, (bytes, bytearray)):
        h.update(b"B" + hashlib.sha256(bytes(x)).digest())
    elif isinstance(x, (list, tuple)):
        h.update(b"L")
        for i in x:
            _forma(i, h)
    elif isinstance(x, dict):
        for k in sorted(x):
            h.update(b"K" + str(k).encode())
            _forma(x[k], h)
    else:
        for at in ("text", "inline_data", "data", "mime_type", "parts", "file_data"):
            if hasattr(x, at):
                h.update(b"A" + at.encode())
                _forma(getattr(x, at), h)


def chave_gemini(modelo, conteudo) -> str:
    h = hashlib.sha256(str(modelo).encode())
    _forma(conteudo, h)
    return h.hexdigest()


# ── falsos do Playwright: aceitam qualquer chamada e nao fazem IO ─────────────
class _Nada:
    """Qualquer metodo desconhecido vira no-op que devolve None."""
    def __getattr__(self, nome):
        return lambda *a, **k: None


class PaginaFalsa(_Nada):
    url = "about:blank"
    keyboard = _Nada()

    def evaluate(self, *a, **k):
        return None

    def query_selector(self, *a, **k):
        return None

    def query_selector_all(self, *a, **k):
        return []


class _ReqFalso:
    url = "https://credenciado.odontoprev.com.br/replay"
    headers = {"authorization": "Bearer REPLAY"}


class ContextoFalso(_Nada):
    def on(self, evento, fn):
        if evento == "request":      # entrega o Bearer a quem escuta (descoberta)
            fn(_ReqFalso())

    def new_page(self):
        return PaginaFalsa()

    def cookies(self):
        return []

    def storage_state(self, *a, **k):
        return {}


class NavegadorFalso(_Nada):
    def new_context(self, *a, **k):
        return ContextoFalso()


class _Chromium:
    def launch(self, *a, **k):
        return NavegadorFalso()


class PlaywrightFalso:
    chromium = _Chromium()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def start(self):
        return self

    def stop(self):
        pass


def trio_falso(*a, **k):
    return NavegadorFalso(), ContextoFalso(), PaginaFalsa()


# ── Gemini ──────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, text):
        self.text = text


def gemini_cliente(modo, cassete, real_cls):
    class _Models:
        def __init__(self, real):
            self._real = real

        def generate_content(self, model=None, contents=None, config=None, **kw):
            k = chave_gemini(model, contents)
            if modo == "tocar":
                return _Resp(cassete.tocar("gemini", k))
            r = self._real.generate_content(model=model, contents=contents, config=config, **kw)
            cassete.gravar("gemini", k, getattr(r, "text", None))
            return r

    class _Cliente:
        def __init__(self, *a, **k):
            # Guarda o CLIENTE real, nao so o .models: sem referencia o cliente e
            # coletado, fecha a conexao e toda chamada vira "Cannot send a request,
            # as the client has been closed" (gravacao de 09/10).
            self._real = real_cls(*a, **k) if modo == "gravar" else None
            self.models = _Models(self._real.models if self._real else None)

    return _Cliente


# ── requests.Session ────────────────────────────────────────────────────────
class _RespHttp:
    def __init__(self, status, conteudo, headers):
        self.status_code = status
        self.content = conteudo
        self.headers = headers or {}
        self.text = conteudo.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


def _chave_http(metodo, url, kw):
    extra = {k: kw[k] for k in ("params", "data", "json") if kw.get(k) is not None}
    return f"{metodo} {url} " + json.dumps(extra, sort_keys=True, default=str)


def sessao_classe(modo, cassete, real_cls):
    class _Sessao:
        def __init__(self, *a, **k):
            self._real = real_cls(*a, **k) if modo == "gravar" else None
            self.headers = self._real.headers if self._real else {}
            self.cookies = self._real.cookies if self._real else _Nada()

        def _faz(self, metodo, url, **kw):
            k = _chave_http(metodo, url, kw)
            if modo == "tocar":
                v = cassete.tocar("http", k)
                return _RespHttp(v["status"], cassete.ler_blob(v["blob"]), v["headers"])
            r = getattr(self._real, metodo.lower())(url, **kw)
            cassete.gravar("http", k, {"status": r.status_code,
                                       "blob": cassete.gravar_blob(r.content),
                                       "headers": dict(r.headers)})
            return r

        def get(self, url, **kw):
            return self._faz("GET", url, **kw)

        def post(self, url, **kw):
            return self._faz("POST", url, **kw)

        def mount(self, *a, **k):
            if self._real:
                self._real.mount(*a, **k)

        def close(self):
            if self._real:
                self._real.close()

    return _Sessao


import contextlib
from datetime import datetime as _dt_real


def _df_para_json(df):
    return df.to_json(orient="split", force_ascii=False)


def _json_para_df(s):
    import io
    import pandas as pd
    return pd.read_json(io.StringIO(s), orient="split", dtype=False)


_COSTURAS = ("sync_playwright", "login_odonto", "_login_playwright", "abrir_consultar_gtos",
             "consultar_periodo", "abrir_gto", "listar_gtos", "_get_relatorio_analitico",
             "_baixa_um", "anexos_do_paciente", "_carregar_confirmados", "datetime",
             "get_credentials")


@contextlib.contextmanager
def instalar(modo: str, cassete: Cassete):
    """Troca as costuras de `esteira` por gravadores (modo 'gravar') ou tocadores
    ('tocar'); restaura ao sair. A lista de costuras e a tabela do plano da Fase 1."""
    import esteira as E
    import google.genai as G
    assert modo in ("gravar", "tocar")
    orig = {n: getattr(E, n) for n in _COSTURAS}
    orig_client, orig_sess = G.Client, E.requests.Session
    tocar = modo == "tocar"

    def _listar_gtos(pg):
        if tocar:
            return cassete.tocar("listar_gtos", "listar_gtos")
        v = orig["listar_gtos"](pg)
        cassete.gravar("listar_gtos", "listar_gtos", v)
        return v

    def _analitico(pg, conv, seg, data):
        if tocar:
            return _json_para_df(cassete.tocar("analitico", data))
        df = orig["_get_relatorio_analitico"](pg, conv, seg, data)
        cassete.gravar("analitico", data, _df_para_json(df))
        return df

    def _baixa(pg, ctx, by_norm, g, tmp, data):
        k = str(g.get("gto"))
        if tocar:
            v = dict(cassete.tocar("baixa_um", k))
            arqs = v.pop("_arquivos", None) or {}
            if "_pasta" in v or arqs:
                pasta = os.path.join(tmp, f"replay_{k}")
                os.makedirs(pasta, exist_ok=True)
                for nome, sha in arqs.items():
                    with open(os.path.join(pasta, nome), "wb") as f:
                        f.write(cassete.ler_blob(sha))
                v["_pasta"] = pasta
            return v
        r = orig["_baixa_um"](pg, ctx, by_norm, g, tmp, data)
        v = {kk: vv for kk, vv in r.items()}
        pasta = r.get("_pasta")
        if pasta and os.path.isdir(pasta):
            v["_arquivos"] = {}
            for nome in sorted(os.listdir(pasta)):
                with open(os.path.join(pasta, nome), "rb") as f:
                    v["_arquivos"][nome] = cassete.gravar_blob(f.read())
        cassete.gravar("baixa_um", k, json.loads(json.dumps(v, default=str)))
        return r

    def _anexos(pg, nome, cod, nascimento=None):
        k = f"{nome}|{cod}|{nascimento}"
        if tocar:
            v = cassete.tocar("anexos_do_paciente", k)
            if isinstance(v, dict) and v.get("_erro"):
                raise RuntimeError(v["_erro"])
            return v
        try:
            v = orig["anexos_do_paciente"](pg, nome, cod, nascimento)
        except Exception as e:
            cassete.gravar("anexos_do_paciente", k, {"_erro": str(e)})
            raise
        cassete.gravar("anexos_do_paciente", k, v)
        return v

    def _confirmados():
        if tocar:
            return set(cassete.tocar("confirmados", "confirmados"))
        v = orig["_carregar_confirmados"]()
        cassete.gravar("confirmados", "confirmados", sorted(v))
        return v

    agora = (_dt_real.fromisoformat(cassete.meta["agora"]) if tocar and cassete.meta.get("agora")
             else _dt_real.now())
    if not tocar:
        cassete.meta["agora"] = agora.isoformat()

    class _Relogio(_dt_real):
        @classmethod
        def now(cls, tz=None):
            return agora if tz is None else agora.astimezone(tz)

    novos = {"listar_gtos": _listar_gtos, "_get_relatorio_analitico": _analitico,
             "_baixa_um": _baixa, "anexos_do_paciente": _anexos,
             "_carregar_confirmados": _confirmados, "datetime": _Relogio}
    if tocar:
        novos.update({"sync_playwright": PlaywrightFalso, "login_odonto": trio_falso,
                      "_login_playwright": trio_falso,
                      "abrir_consultar_gtos": lambda *a, **k: None,
                      "consultar_periodo": lambda *a, **k: None,
                      "abrir_gto": lambda *a, **k: PaginaFalsa(),
                      "get_credentials": lambda *a, **k: ("replay", "replay")})
    try:
        for n, f in novos.items():
            setattr(E, n, f)
        G.Client = gemini_cliente(modo, cassete, orig_client)
        E.requests.Session = sessao_classe(modo, cassete, orig_sess)
        yield cassete
    finally:
        for n, f in orig.items():
            setattr(E, n, f)
        G.Client, E.requests.Session = orig_client, orig_sess
        if not tocar:
            cassete.salvar()


def veredito(resumo: dict) -> dict:
    """gto -> o que importa para faturamento, em forma comparavel."""
    out = {}
    for d in sorted((resumo or {}).get("decisoes") or [], key=lambda x: str(x.get("gto"))):
        g = str(d.get("gto"))
        motivo = ((d.get("gemini") or {}).get("motivo")) or d.get("erro") or ""
        out[g] = {"categoria": d.get("categoria"), "anexado": d.get("anexado"),
                  "arquivos": sorted(os.path.basename(str(a))
                                     for a in (d.get("arquivos_anexados") or [])),
                  "motivo": str(motivo)}
    return out
