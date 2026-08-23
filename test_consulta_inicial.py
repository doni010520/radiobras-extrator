"""Worker de anexacao sem lista carregada nao pode culpar guia por guia.

Incidente real, execucao #613 (397950, 18/08), lido no log:

    [124s] [ANEX1] consulta inicial falhou: Page.click: Timeout 8000ms exceeded
                   waiting for locator("text=Per...")      <- "Periodo"
    [125s] [ANEX1] GTO 196329949 ERRO Linha da GTO 196329949 nao encontrada.
    [127s] [ANEX1] GTO 196350383 ERRO Linha da GTO 196350383 nao encontrada.
    [128s] [ANEX1] GTO 196348379 ERRO ...
    ... 8 guias, todas com a mesma mensagem

A consulta inicial do worker falhou — a lista de GTOs nunca carregou. A excecao era
LOGADA E ENGOLIDA (esteira.py:2967-2970) e o worker seguia para a fila. Ai TODA guia
falhava, uma a uma, com uma mensagem que culpa a guia ("linha nao encontrada")
quando o problema era do worker.

Duas correcoes: a consulta ganha RETRY (era uma tentativa so, contra um timeout de
8s que e claramente transitorio), e se ainda assim falhar o worker DESISTE em vez de
processar a fila — as guias ficam para outro worker, com a causa honesta."""
import pytest

from esteira import _consulta_inicial


class _Pagina:
    """Pagina falsa: falha nas N primeiras tentativas."""
    def __init__(self, falhas=0):
        self.falhas, self.chamadas = falhas, 0


def _monta(monkeypatch, falhas):
    estado = {"n": 0}

    def _abrir(pg):
        estado["n"] += 1
        if estado["n"] <= falhas:
            raise RuntimeError("Page.click: Timeout 8000ms exceeded")

    def _periodo(pg, data):
        return None
    import esteira
    monkeypatch.setattr(esteira, "abrir_consultar_gtos", _abrir)
    monkeypatch.setattr(esteira, "consultar_periodo", _periodo)
    return estado


def test_primeira_tentativa_ok(monkeypatch):
    estado = _monta(monkeypatch, falhas=0)
    ok, erro = _consulta_inicial(_Pagina(), "18/08/2026", _sleep=lambda s: None)
    assert ok is True and erro == ""
    assert estado["n"] == 1


def test_recupera_na_segunda(monkeypatch):
    """Timeout de 8s e transitorio — insistir e barato. Antes era 1 tentativa so."""
    estado = _monta(monkeypatch, falhas=1)
    ok, erro = _consulta_inicial(_Pagina(), "18/08/2026", _sleep=lambda s: None)
    assert ok is True
    assert estado["n"] == 2


def test_desiste_depois_do_teto_e_diz_a_causa_REAL(monkeypatch):
    """Se nao carregou a lista, o worker tem que parar. Processar a fila assim
    marca 8 guias boas como 'linha nao encontrada' — culpa na guia errada."""
    _monta(monkeypatch, falhas=99)
    ok, erro = _consulta_inicial(_Pagina(), "18/08/2026", _sleep=lambda s: None)
    assert ok is False
    assert "Timeout" in erro          # a causa real, nao "linha nao encontrada"


def test_nunca_levanta(monkeypatch):
    """Quem chama esta dentro do worker de anexacao: uma excecao aqui derrubaria
    a thread e as guias sumiriam da fila sem aviso."""
    _monta(monkeypatch, falhas=99)
    ok, erro = _consulta_inicial(_Pagina(), "18/08/2026", _sleep=lambda s: None)
    assert isinstance(ok, bool) and isinstance(erro, str)
