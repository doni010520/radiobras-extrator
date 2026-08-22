"""Execucao que ABORTOU inteira volta pra fila como o DIA INTEIRO.

Furo fechado em 22/08: login/proxy/Gemini fora derrubava a rodada e o dia nao
faturava em silencio — sem pendencia, sem fila, sem ninguem avisado. Agora o dia
entra na mesma fila de retry, com o mesmo backoff e o mesmo teto. Como um aborto
nao tem guia nenhuma (nao chegou a decidir nada), a chave e uma sentinela e o
worker precisa rodar o DIA TODO, nao `apenas_gtos`."""
import db
import esteira


def _fila(monkeypatch, devidos):
    monkeypatch.setattr(db, "retries_devidos", lambda limite=50: devidos)
    monkeypatch.setattr(db, "bump_retry", lambda g: None)
    monkeypatch.setattr(db, "get_portal_senha", lambda c: "senha")
    monkeypatch.setattr(db, "salvar_execucao", lambda r, l: 1)
    chamadas = []

    def _fake(dia, *a, **kw):
        chamadas.append({"dia": dia, "apenas_gtos": kw.get("apenas_gtos"),
                         "conta": kw.get("conta")})
        return {"data": dia, "conta": kw.get("conta"), "decisoes": [], "dry_run": False}
    monkeypatch.setattr(esteira, "rodar_esteira", _fake)
    return chamadas


def test_sentinela_do_dia_reroda_o_dia_inteiro(monkeypatch):
    g = db._gto_dia("388336", "18/08/2026")
    chamadas = _fila(monkeypatch, [{"gto": g, "conta": "388336",
                                    "dia": "18/08/2026", "tentativas": 0}])
    esteira.processar_retries()
    assert len(chamadas) == 1
    assert chamadas[0]["dia"] == "18/08/2026"
    # apenas_gtos=None => dia inteiro. Mandar a sentinela como se fosse guia faria a
    # esteira procurar uma GTO chamada "__DIA__..." e nao faturar nada.
    assert chamadas[0]["apenas_gtos"] is None


def test_guias_normais_seguem_direcionadas(monkeypatch):
    chamadas = _fila(monkeypatch, [
        {"gto": "195831154", "conta": "388336", "dia": "18/08/2026", "tentativas": 1},
        {"gto": "195904169", "conta": "388336", "dia": "18/08/2026", "tentativas": 1}])
    esteira.processar_retries()
    assert chamadas[0]["apenas_gtos"] == ["195831154", "195904169"]


def test_dia_abortado_engole_as_guias_do_mesmo_dia(monkeypatch):
    # se o dia todo vai rodar de novo, nao faz sentido uma passada dirigida antes
    g = db._gto_dia("388336", "18/08/2026")
    chamadas = _fila(monkeypatch, [
        {"gto": "195831154", "conta": "388336", "dia": "18/08/2026", "tentativas": 1},
        {"gto": g, "conta": "388336", "dia": "18/08/2026", "tentativas": 0}])
    esteira.processar_retries()
    assert len(chamadas) == 1
    assert chamadas[0]["apenas_gtos"] is None
