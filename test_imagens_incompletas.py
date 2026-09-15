"""Captura PARCIAL das folhas de imagem nao pode faturar como se fosse completa.

Caso RONALDO SOUZA DA PAZ (GTO 197268351, doc. ortodontica completa, 12/09),
faturado em 15/09 12:37: o PRORADIS tinha 4 folhas com a logo (fotos, panoramica,
tracado cefalometrico e analise de Ricketts) e subiram SO 2. A clinica percebeu e
anexou as outras duas a mao as 14:50. Reproduzido na mesma tarde, isolado: as 4
folhas chegam em ~5s, por qualquer das 4 linhas da worklist, com espera curta ou
longa, e o dedup perceptual nao descarta nenhuma (Hamming 13-41, limiar 4).

A causa nao e o tempo nem o dedup: e o robo ACEITAR uma captura parcial em
silencio. Sob carga (6 navegadores baixando folhas de ~700 KB ao mesmo tempo),
`on_resp` engolia o `body()` que falhava (`except: return`), a imagem que nao
terminou antes de fechar a janela simplesmente nao existia, e `_processar_paciente`
parava na primeira linha com `qtd > 0`. A guia seguia para o anexador com metade da
documentacao — mesmo tipo de furo da UATILA.

Regra: so vale captura COMPLETA (toda imagem pedida chegou e foi lida). Incompleta
-> tenta a proxima linha; nenhuma completa -> falha tecnica NOSSA, nao anexa, entra
no retry.
"""
import cv2
import numpy as np
import pytest

import db
import esteira
import extrator_arquivos as ea


# ── imagens sinteticas no padrao de entrega (logo verde no canto) ──────────
def _jpeg_com_logo(semente: int) -> bytes:
    rng = np.random.default_rng(semente)
    img = (rng.integers(0, 255, size=(300, 400, 3))).astype(np.uint8)
    img[0:40, 0:120] = (40, 200, 40)          # BGR: verde forte no cabecalho
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


# ── fakes do Playwright ──────────────────────────────────────────────────────
class _Req:
    def __init__(self, url):
        self.url = url


class _Resp:
    def __init__(self, url, body):
        self.url = url
        self.request = _Req(url)
        self._body = body

    def body(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Popup:
    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        pass

    def close(self):
        pass


class _ExpectPage:
    def __init__(self, popup):
        self.value = popup

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Ctx:
    """Contexto que dispara um roteiro de eventos quando o popup e aberto."""

    def __init__(self, roteiro):
        self.roteiro = roteiro
        self.listeners = {}

    def on(self, evento, fn):
        self.listeners.setdefault(evento, []).append(fn)

    def remove_listener(self, evento, fn):
        self.listeners.get(evento, []).remove(fn)

    def expect_page(self, timeout=None):
        return _ExpectPage(_Popup())

    def disparar(self):
        for ev in self.roteiro:
            tipo, url = ev[0], ev[1]
            if tipo == "request":
                arg = _Req(url)
            elif tipo == "response":
                arg = _Resp(url, ev[2])
            elif tipo == "requestfailed":
                arg = _Req(url)
            for fn in list(self.listeners.get(tipo, [])):
                fn(arg)


class _Page:
    def __init__(self, ctx):
        self.ctx = ctx

    def evaluate(self, js, args=None):
        self.ctx.disparar()

    def wait_for_timeout(self, ms):
        pass


U1 = "https://x/viewer/u/image?studyUID=1.2.3.1"
U2 = "https://x/viewer/u/image?studyUID=1.2.3.2"


def _baixar(roteiro, tmp_path, monkeypatch):
    monkeypatch.setattr(ea, "IMG_PENDENTES_MAX_MS", 50)
    ctx = _Ctx(roteiro)
    return ea.baixar_imagens(_Page(ctx), ctx, "s", "sc", str(tmp_path), set(), 0)


# ── 1. baixar_imagens diz se a captura veio COMPLETA ─────────────────────────
def test_todas_as_imagens_chegam_captura_completa(tmp_path, monkeypatch):
    r = _baixar([("request", U1), ("request", U2),
                 ("response", U1, _jpeg_com_logo(1)),
                 ("response", U2, _jpeg_com_logo(2))], tmp_path, monkeypatch)
    assert r["qtd"] == 2
    assert r["completa"] is True


def test_imagem_que_falha_no_caminho_torna_a_captura_incompleta(tmp_path, monkeypatch):
    r = _baixar([("request", U1), ("request", U2),
                 ("response", U1, _jpeg_com_logo(1)),
                 ("requestfailed", U2)], tmp_path, monkeypatch)
    assert r["qtd"] == 1
    assert r["completa"] is False


def test_corpo_que_nao_se_consegue_ler_torna_a_captura_incompleta(tmp_path, monkeypatch):
    """Era o `except: return` silencioso do on_resp."""
    r = _baixar([("request", U1), ("request", U2),
                 ("response", U1, _jpeg_com_logo(1)),
                 ("response", U2, RuntimeError("Target closed"))], tmp_path, monkeypatch)
    assert r["completa"] is False


def test_imagem_que_nunca_chega_torna_a_captura_incompleta(tmp_path, monkeypatch):
    """Pedida, mas a janela fechou antes da resposta."""
    r = _baixar([("request", U1), ("request", U2),
                 ("response", U1, _jpeg_com_logo(1))], tmp_path, monkeypatch)
    assert r["completa"] is False


# ── 2. _processar_paciente so aceita captura completa ────────────────────────
def _paciente(tmp_path, monkeypatch, capturas):
    """capturas: {study_id: dict de retorno de baixar_imagens}"""
    chamadas = []

    def _fake_baixar(page, ctx, study_id, schedule_id, out_dir, seen, n):
        chamadas.append(study_id)
        return dict(capturas[study_id])

    monkeypatch.setattr(ea, "baixar_imagens", _fake_baixar)
    monkeypatch.setattr(ea, "extrair_tokens", lambda h: {
        "pan": [], "ceph": [], "exame": "PANORAMICA",
        "doc": {"study_id": h, "schedule_id": "sc"}})
    monkeypatch.setattr(ea, "baixar_laudos", lambda *a, **k: [
        {"status": "OK", "exame": "PANORAMICA"}])
    wl = [{"accession": "1", "rows_html": ["A", "B"]}]
    pac = {"nome": "RONALDO SOUZA DA PAZ", "cod_pac": "WL1", "accessions": ["1"]}
    res = ea._processar_paciente(None, None, pac, wl, str(tmp_path), "12/09/2026")
    return res, chamadas


def _cap(qtd, completa):
    return {"qtd": qtd, "arquivos": [f"ENTREGA_{i}.jpg" for i in range(qtd)],
            "pendencias": [], "next_n": qtd, "total_capturadas": qtd,
            "completa": completa}


def test_captura_parcial_tenta_a_proxima_linha_e_fica_com_a_completa(tmp_path, monkeypatch):
    res, chamadas = _paciente(tmp_path, monkeypatch,
                              {"A": _cap(2, False), "B": _cap(4, True)})
    assert chamadas == ["A", "B"]
    assert res["imagens"]["qtd"] == 4
    assert not res.get("imagens_incompletas")


def test_captura_completa_na_primeira_linha_nao_gasta_outra_chamada(tmp_path, monkeypatch):
    res, chamadas = _paciente(tmp_path, monkeypatch,
                              {"A": _cap(4, True), "B": _cap(4, True)})
    assert chamadas == ["A"]
    assert not res.get("imagens_incompletas")


def test_nenhuma_captura_completa_e_falha_tecnica_nossa(tmp_path, monkeypatch):
    res, _ = _paciente(tmp_path, monkeypatch,
                       {"A": _cap(2, False), "B": _cap(2, False)})
    assert res["imagens_incompletas"] is True
    assert res["status"] != "OK"
    assert any("falha técnica" in p for p in res["pendencias"])


# ── 3. a esteira NAO anexa download incompleto ───────────────────────────────
def test_download_incompleto_vira_erro_e_nao_segue_para_anexar():
    st, erro = esteira._status_download(
        {"imagens_incompletas": True,
         "pendencias": ["falha técnica: 2 folha(s) de imagem não carregaram"]}, nf=5)
    assert st == "ERRO"
    assert "falha técnica" in erro


def test_download_completo_segue_como_antes():
    assert esteira._status_download({}, nf=5) == ("BAIXADO", None)
    assert esteira._status_download({}, nf=0) == ("SEM_ARQUIVOS", None)


def test_o_erro_de_download_incompleto_e_classificado_como_falha_nossa():
    """Regra do dono: falha nossa sai do painel da clinica e nos refazemos."""
    _st, erro = esteira._status_download(
        {"imagens_incompletas": True,
         "pendencias": ["falha técnica: 2 folha(s) de imagem não carregaram"]}, nf=5)
    motivo = ("NÃO FATUROU por falha técnica nossa, não da clínica nem do "
              "radiologista — o processamento desta guia foi interrompido. "
              f"O QUE FAZER: reprocessar o dia. Detalhe técnico: {erro}")
    assert db.eh_nosso(motivo, "erro") is True
