"""Testes do login Hapvida — lógica pura, sem rede."""
import pytest

import extrator_hapvida as hv


def test_resposta_ok_extrai_sessao():
    corpo = ('{"status":"0","url":"webNewDentalPrestador.pr_prestador?pidsessao=241307094126613201549'
             '&pnocache=0713242641&pcdpessoa=121555964","msg":"","callback":""}')
    info = hv.interpretar_resposta_login(corpo)
    assert info["ok"] is True
    assert info["id_sessao"] == "241307094126613201549"
    assert info["no_cache"] == "0713242641"
    assert info["cd_pessoa"] == "121555964"


def test_resposta_com_msg_de_erro_nao_e_ok():
    corpo = '{"status":"1","url":"webNewDentalPrestador.pr_login","msg":"Senha inválida","callback":""}'
    info = hv.interpretar_resposta_login(corpo)
    assert info["ok"] is False
    assert info["msg"] == "Senha inválida"


def test_status_zero_sem_sessao_nao_e_ok():
    # status 0 mas sem pidsessao na URL: não confiar
    info = hv.interpretar_resposta_login('{"status":"0","url":"webNewDentalPrestador.pr_prestador","msg":""}')
    assert info["ok"] is False


def test_resposta_nao_json():
    assert hv.interpretar_resposta_login("<html>erro</html>")["ok"] is False


def test_home_logada_devolve_prestador():
    html = "<p>Bem Vindo(a) <b>RADIOBRAS RADIOLOGIA ODONTOLOGICA DIGITAL LTDA</b> ao seu portal exclusivo.</p>"
    assert hv.pagina_logada(html) == "RADIOBRAS RADIOLOGIA ODONTOLOGICA DIGITAL LTDA"


def test_sessao_expirada_nao_e_logada():
    assert hv.pagina_logada("ATENCÃO: SESSÃO EXPIRADA OU INEXISTENTE. Bem Vindo(a) X ao seu portal") is None


def test_listar_contas(monkeypatch):
    monkeypatch.setenv("HAPVIDA_CONTAS", " Centro, tancredo ,,ITAIGARA ")
    assert hv.listar_contas_hapvida() == ["centro", "tancredo", "itaigara"]


def test_credencial_ausente(monkeypatch):
    monkeypatch.delenv("HAPVIDA_XPTO_USER", raising=False)
    monkeypatch.delenv("HAPVIDA_XPTO_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="HAPVIDA_XPTO_USER"):
        hv.get_credentials_hapvida("xpto")


def test_credencial_normaliza_cnpj(monkeypatch):
    monkeypatch.setenv("HAPVIDA_TESTE_USER", "26.402.304/0001-79")
    monkeypatch.setenv("HAPVIDA_TESTE_PASSWORD", "x")
    assert hv.get_credentials_hapvida("teste") == ("26402304000179", "x")


def test_url_da_sessao_carrega_parametros():
    s = hv.HapvidaSessao(conta="c", http=None, id_sessao="1", no_cache="2", cd_pessoa="3")
    u = s.url("webNewDentalPrestador.pr_Recurso_Glosa", pProcesso="99")
    assert u.startswith(hv.BASE + "webNewDentalPrestador.pr_Recurso_Glosa?")
    assert "pIdSessao=1" in u and "pNoCache=2" in u and "pCDPessoa=3" in u and "pProcesso=99" in u
