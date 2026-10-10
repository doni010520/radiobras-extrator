"""Saida reserva para o OdontoPrev (10/10).

A saida pelo PC da clinica (Tailscale) cai de madrugada e no fim de semana. Com ela
fora, o login nem chegava a tela e a rodada inteira morria. ODONTO_PROXY_URL aceita
varias saidas em ordem de preferencia; a que logou vira a ativa e as chamadas HTTP
seguem por ela. Com uma saida so, nada muda."""
import pytest

import extrator_odontoprev as eo

TS = "http://127.0.0.1:1056"
PAGO = "http://u:s@pago.example:8000"


@pytest.fixture(autouse=True)
def _limpa(monkeypatch):
    monkeypatch.setattr(eo, "_proxy_ativo", None)
    yield


def _login_falso(monkeypatch, falham):
    tentadas = []

    def via(pw, user, password, url):
        tentadas.append(url)
        if url in falham:
            raise RuntimeError(f"Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED via {url}")
        return ("browser", "ctx", "page")

    monkeypatch.setattr(eo, "_login_odonto_via", via)
    return tentadas


def test_lista_em_ordem(monkeypatch):
    monkeypatch.setenv("ODONTO_PROXY_URL", f"{TS}, {PAGO}")
    assert eo._odo_proxy_urls() == [TS, PAGO]


def test_principal_ok_nem_toca_na_reserva(monkeypatch):
    monkeypatch.setenv("ODONTO_PROXY_URL", f"{TS},{PAGO}")
    t = _login_falso(monkeypatch, falham=[])
    assert eo.login_odonto(None, "u", "p") == ("browser", "ctx", "page")
    assert t == [TS] and eo._odo_proxy_url() == TS


def test_principal_fora_loga_pela_reserva_e_http_segue_por_ela(monkeypatch):
    monkeypatch.setenv("ODONTO_PROXY_URL", f"{TS},{PAGO}")
    t = _login_falso(monkeypatch, falham=[TS])
    eo.login_odonto(None, "u", "p")
    assert t == [TS, PAGO]
    assert eo._odo_proxy_url() == PAGO
    assert "pago.example:8000" in eo._odo_requests_proxies()["https"]


def test_todas_fora_propaga_o_erro_real(monkeypatch):
    """O motivo da falha (proxy/rede/senha) sai do texto do erro: tem de ser o REAL."""
    monkeypatch.setenv("ODONTO_PROXY_URL", f"{TS},{PAGO}")
    _login_falso(monkeypatch, falham=[TS, PAGO])
    with pytest.raises(RuntimeError, match="ERR_TUNNEL"):
        eo.login_odonto(None, "u", "p")


def test_uma_saida_so_comporta_como_antes(monkeypatch):
    monkeypatch.setenv("ODONTO_PROXY_URL", TS)
    t = _login_falso(monkeypatch, falham=[TS])
    with pytest.raises(RuntimeError):
        eo.login_odonto(None, "u", "p")
    assert t == [TS]


def test_sem_proxy_vai_direto(monkeypatch):
    monkeypatch.delenv("ODONTO_PROXY_URL", raising=False)
    t = _login_falso(monkeypatch, falham=[])
    eo.login_odonto(None, "u", "p")
    assert t == [""]
    assert eo._odo_requests_proxies() is None and eo._odo_playwright_proxy() is None


def test_ativa_que_saiu_da_lista_nao_e_usada(monkeypatch):
    monkeypatch.setattr(eo, "_proxy_ativo", PAGO)
    monkeypatch.setenv("ODONTO_PROXY_URL", TS)
    assert eo._odo_proxy_url() == TS
