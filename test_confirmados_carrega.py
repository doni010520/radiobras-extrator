"""BUG crítico (achado 17/08): rodar_esteira carregava as confirmações do 'sinal
verde humano' com db.confirmacoes_set(), mas 'db' NÃO estava importado no escopo ->
NameError silencioso (engolido pelo except) -> _confirmados sempre VAZIO -> o botão
'✔ Confirmei' NUNCA liberava a guia (feature morta desde 13/08). _carregar_confirmados
faz o import local correto e é testável."""
import esteira
import db


def test_carregar_confirmados_le_do_db(monkeypatch):
    # se 'db' estiver acessível, o conjunto do banco chega inteiro (o bug fazia vir vazio)
    monkeypatch.setattr(db, "confirmacoes_set", lambda: {"111", "222"})
    assert esteira._carregar_confirmados() == {"111", "222"}


def test_carregar_confirmados_nunca_derruba_a_esteira(monkeypatch):
    # falha REAL do banco -> set() vazio (esteira segue), mas isso é por erro de banco,
    # não por NameError de import (que era o bug — mascarava confirmação existente).
    def _boom():
        raise RuntimeError("db fora do ar")
    monkeypatch.setattr(db, "confirmacoes_set", _boom)
    assert esteira._carregar_confirmados() == set()
