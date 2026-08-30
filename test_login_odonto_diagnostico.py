"""Falha ao ALCANCAR o portal nao e senha errada.

Rodada de 30/08, Tancredo, dia 22/08: a execucao abortou com "Login no
RedeUna/OdontoPrev falhou para o codigo 397950 — verifique/cadastre a senha do
portal". O detalhe real era `Page.goto: Timeout 60000ms exceeded` navegando ate
credenciado.odontoprev.com.br: o robo nao chegou nem a ver o formulario. Tres
minutos depois a mesma conta logou normalmente.

Mandar conferir a senha nesse caso queima tempo da operacao no lugar errado — e e
o mesmo erro que ja tinha sido documentado quando o bloqueio de IP se disfarcava
de "usuario ou senha invalidos"."""
from esteira import _motivo_login_odonto

_TIMEOUT = ('Page.goto: Timeout 60000ms exceeded. Call log: - navigating to '
            '"https://credenciado.odontoprev.com.br/", waiting until "domcontentloaded"')
_PROXY = "net::ERR_PROXY_CONNECTION_FAILED at https://credenciado.odontoprev.com.br/"
_REDE = "net::ERR_CONNECTION_RESET"
_SENHA = "campo de usuario aceito mas retornou 'Usuario ou senha invalidos'"


def test_timeout_de_navegacao_nao_culpa_a_senha():
    m = _motivo_login_odonto("397950", _TIMEOUT).lower()
    # o que importa nao e a palavra "senha" (a mensagem diz "NAO e a senha"),
    # e sim nao MANDAR a operacao mexer nela
    assert "verifique/cadastre a senha" not in m
    assert "não é a senha" in m
    assert "alcançar" in m


def test_proxy_continua_apontando_o_proxy():
    m = _motivo_login_odonto("388336", _PROXY)
    assert "proxy" in m.lower()


def test_erro_de_rede_e_conectividade():
    m = _motivo_login_odonto("410923", _REDE).lower()
    assert "verifique/cadastre a senha" not in m
    assert "alcançar" in m


def test_credencial_recusada_continua_pedindo_a_senha():
    """O caminho legitimo nao pode regredir: quando o portal RESPONDE e recusa,
    a senha e mesmo a suspeita."""
    m = _motivo_login_odonto("397950", _SENHA)
    assert "senha do portal" in m.lower()


def test_o_detalhe_tecnico_sempre_acompanha():
    for det in (_TIMEOUT, _PROXY, _SENHA):
        assert "Detalhe" in _motivo_login_odonto("397950", det)
