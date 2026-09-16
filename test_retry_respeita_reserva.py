"""O retry automatico NAO pode rodar o mesmo (dia, conta) junto com outra esteira.

INCIDENTE 16/09/2026: o loop de retry (`processar_retries`) reprocessou 14/09
Centro/Lauro (exec 1231) ao mesmo tempo que um "Faturar dia" (exec 1232). As duas
leram os anexos antes de a outra gravar, concluiram "falta anexar" e ENVIARAM — tres
guias ficaram com laudo, imagem e pedido em DOBRO no OdontoPrev (ROSSAN 197295804,
ADNA 197286933, DANIELA 197298001). O portal nao remove anexo: e permanente.

`app._esteira_reservar(dia, conta)` existe exatamente para isso e ja protege o
/faturar/run e o cron — o retry chamava `rodar_esteira` direto, sem reservar.

Regra: reserva negada -> o grupo e PULADO sem gastar tentativa (volta no proximo
ciclo); reserva concedida -> roda e LIBERA no fim, mesmo se a rodada explodir.
"""
import inspect

import db
import esteira


def _fila(monkeypatch, devidos, rodar=None):
    monkeypatch.setattr(db, "retry_pausado", lambda: False)
    monkeypatch.setattr(db, "retries_devidos", lambda limite=50: devidos)
    bumps = []
    monkeypatch.setattr(db, "bump_retry", lambda g: bumps.append(g))
    monkeypatch.setattr(db, "desfazer_bump", lambda g: None)
    monkeypatch.setattr(db, "get_portal_senha", lambda c: "senha")
    monkeypatch.setattr(db, "salvar_execucao", lambda r, l: 1)
    monkeypatch.setattr(db, "rodada_foi_apagao", lambda d: False)
    chamadas = []

    def _fake(dia, *a, **kw):
        chamadas.append((dia, kw.get("conta")))
        if rodar:
            rodar()
        return {"data": dia, "conta": kw.get("conta"), "decisoes": [], "dry_run": False}

    monkeypatch.setattr(esteira, "rodar_esteira", _fake)
    return chamadas, bumps


GUIA = [{"gto": "197295804", "conta": "388336", "dia": "14/09/2026", "tentativas": 1}]


def test_reserva_negada_nao_roda_e_nao_gasta_tentativa(monkeypatch):
    chamadas, bumps = _fila(monkeypatch, GUIA)
    liberou = []
    res = esteira.processar_retries(
        reservar=lambda dia, conta, tag: None,
        liberar=lambda dia, conta, tag: liberou.append((dia, conta)))
    assert chamadas == []
    assert bumps == []
    assert liberou == []
    assert res.get("pulados") == 1


def test_reserva_concedida_roda_e_libera(monkeypatch):
    chamadas, bumps = _fila(monkeypatch, GUIA)
    reservou, liberou = [], []

    def _reservar(dia, conta, tag):
        reservou.append((dia, conta)); return tag

    esteira.processar_retries(reservar=_reservar,
                              liberar=lambda dia, conta, tag: liberou.append((dia, conta, tag)))
    assert reservou == [("14/09/2026", "388336")]
    assert chamadas == [("14/09/2026", "388336")]
    assert bumps == ["197295804"]
    assert liberou and liberou[0][:2] == ("14/09/2026", "388336")


def test_libera_a_reserva_mesmo_se_a_rodada_explodir(monkeypatch):
    def _boom():
        raise RuntimeError("Page.goto: Timeout 60000ms exceeded")
    _fila(monkeypatch, GUIA, rodar=_boom)
    monkeypatch.setattr(db, "eh_falha_global", lambda e: False)
    liberou = []
    esteira.processar_retries(reservar=lambda dia, conta, tag: tag,
                              liberar=lambda dia, conta, tag: liberou.append(tag))
    assert len(liberou) == 1


def test_scheduler_do_app_passa_a_mesma_reserva_do_faturar():
    import app
    src = inspect.getsource(app._retry_scheduler)
    assert "reservar=_esteira_reservar" in src
    assert "liberar=_esteira_liberar" in src
