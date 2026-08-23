"""A fila tecnica e a tela do DONO. O pessoal da RadioBras nao alcanca.

Regra dele (22/08): "se e falha de sistema, fica para mim; nao pode cair para a
cliente ver". Os usuarios reais do app: andrea, jordon e diana sao role=user; so o
dono e admin. Este teste existe para o dia em que alguem promover um usuario a
admin sem lembrar do que isso abre — ou trocar o guard da rota por engano."""
import os

import pytest


@pytest.fixture()
def app_cli(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "teste")
    import app as appmod
    appmod.app.config["TESTING"] = True
    return appmod


def _logado(appmod, monkeypatch, role, uid):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
        s["username"] = "andrea" if role == "user" else "admin"
        s["role"] = role
    monkeypatch.setattr(appmod, "_usuario_valido", lambda u: {"id": u, "role": role})
    return c


def test_operacao_nao_entra(app_cli, monkeypatch):
    """A Andrea e role=user. 403, nao 200 com conteudo escondido — se a tela
    carregasse e o template e que escondesse, uma mudanca no template vazaria."""
    r = _logado(app_cli, monkeypatch, "user", 2).get("/tecnico")
    assert r.status_code == 403


def test_dono_entra(app_cli, monkeypatch):
    r = _logado(app_cli, monkeypatch, "admin", 1).get("/tecnico")
    assert r.status_code == 200
    assert "Fila t" in r.data.decode("utf8", "replace")


def test_sem_login_nem_chega(app_cli):
    c = app_cli.app.test_client()
    r = c.get("/tecnico")
    assert r.status_code in (302, 401)


def test_a_tela_da_operacao_continua_sem_a_fila_tecnica(app_cli, monkeypatch):
    """O outro lado da regra: /relatorios/pendencias nao pode mostrar a secao
    tecnica para quem nao e admin."""
    c = _logado(app_cli, monkeypatch, "user", 2)
    h = c.get("/relatorios/pendencias?data=2026-08-18").data.decode("utf8", "replace")
    assert "Fila técnica — não é tarefa" not in h
    assert "fila técnica (nossa)" not in h
