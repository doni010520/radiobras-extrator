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
