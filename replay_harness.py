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
            real = real_cls(*a, **k).models if modo == "gravar" else None
            self.models = _Models(real)

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
