"""Download do render do MODELO: espera por condicao, nao por relogio.

Caso NICOLAS RAMOS FANELI (197564266, guia de 21/09): seis rodadas de 22 a 25/09
devolveram ZERO arquivo do modelo; reproduzido em 08/10, as 6 vistas chegam. O
`baixar_entregavel_modelo` esperava 10 s fixos e parava de escutar: o que nao tinha
chegado ate ali nao existia, e a guia virava "cobrar o laudo do radiologista" — de
um exame que nao tem laudo. Mesma falha que o baixar_imagens tinha antes de 15/09
(caso RONALDO), so que no caminho do modelo, que ficou de fora daquele conserto.
"""
import cv2
import numpy as np

import extrator_arquivos as ea


def _jpeg(semente: int) -> bytes:
    rng = np.random.default_rng(semente)
    img = (rng.integers(0, 255, size=(270, 480, 3))).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


class _Req:
    def __init__(self, url):
        self.url = url


class _Resp:
    def __init__(self, url, body):
        self.url = url
        self._body = body

    def body(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Pop:
    def __init__(self, ctx):
        self.ctx = ctx

    def goto(self, *a, **k):
        pass

    def evaluate(self, js, args=None):
        self.ctx.disparar()

    def wait_for_timeout(self, ms):
        pass

    def close(self):
        pass


class _Ctx:
    def __init__(self, roteiro):
        self.roteiro = roteiro
        self.listeners = {}

    def on(self, ev, fn):
        self.listeners.setdefault(ev, []).append(fn)

    def remove_listener(self, ev, fn):
        self.listeners.get(ev, []).remove(fn)

    def new_page(self):
        return _Pop(self)

    def disparar(self):
        for ev in self.roteiro:
            tipo, url = ev[0], ev[1]
            arg = _Resp(url, ev[2]) if tipo == "response" else _Req(url)
            for fn in list(self.listeners.get(tipo, [])):
                fn(arg)


U = [f"https://x/viewer/u/image?studyUID=1.2.3&seriesUID=2.25.{i}" for i in range(4)]


def _baixar(roteiro, tmp_path, monkeypatch):
    monkeypatch.setattr(ea, "IMG_PENDENTES_MAX_MS", 50)
    monkeypatch.setattr(ea, "MODELO_SEM_PEDIDO_MS", 50)
    ctx = _Ctx(roteiro)
    return ea.baixar_entregavel_modelo(None, ctx, "study", str(tmp_path), set(), 0)


def test_todas_as_vistas_chegam_captura_completa(tmp_path, monkeypatch):
    rot = [("request", u) for u in U] + [("response", u, _jpeg(i)) for i, u in enumerate(U)]
    r = _baixar(rot, tmp_path, monkeypatch)
    assert r["qtd"] == 4
    assert r["completa"] is True


def test_vista_pedida_que_nao_chega_torna_incompleta(tmp_path, monkeypatch):
    rot = [("request", u) for u in U] + [("response", U[0], _jpeg(0))]
    r = _baixar(rot, tmp_path, monkeypatch)
    assert r["completa"] is False


def test_vista_que_falha_torna_incompleta(tmp_path, monkeypatch):
    rot = [("request", U[0]), ("request", U[1]),
           ("response", U[0], _jpeg(0)), ("requestfailed", U[1])]
    r = _baixar(rot, tmp_path, monkeypatch)
    assert r["completa"] is False


def test_corpo_ilegivel_torna_incompleta(tmp_path, monkeypatch):
    rot = [("request", U[0]), ("response", U[0], RuntimeError("Target closed"))]
    r = _baixar(rot, tmp_path, monkeypatch)
    assert r["completa"] is False


def test_render_nao_gerado_e_vazio_mas_completo(tmp_path, monkeypatch):
    """Nada pedido = o render ainda nao existe (caso LUIZA 20/08). Isso e pendencia
    legitima, nao falha nossa: completa, com zero arquivo."""
    r = _baixar([], tmp_path, monkeypatch)
    assert r["qtd"] == 0
    assert r["completa"] is True
