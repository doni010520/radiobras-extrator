"""Hapvida ligado ao app: cron, tela e as travas que impedem o OdontoPrev de rodar
para uma conta Hapvida (e vice-versa)."""
import datetime as dt
import logging

import db
import hapvida_app


def _sqlite(tmp_path, monkeypatch, *tabelas):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    eng = create_engine(f"sqlite:///{tmp_path}/t.db")
    db.Base.metadata.create_all(eng, tables=[t.__table__ for t in tabelas])
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng))


def test_dias_do_cron_sao_os_recentes_mais_pendencias():
    hoje = dt.date(2026, 9, 25)
    assert hapvida_app.dias_do_cron(hoje, n=3) == ["22/09/2026", "23/09/2026", "24/09/2026"]
    assert hapvida_app.dias_do_cron(hoje, {"19/09/2026", "23/09/2026"}, n=2) == \
        ["19/09/2026", "23/09/2026", "24/09/2026"]


def test_pendencia_hapvida_nao_entra_no_cron_do_odontoprev(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch, db.Pendencia)
    hoje = dt.date.today().strftime("%d/%m/%Y")
    with db.SessionLocal() as s:
        s.add_all([db.Pendencia(gto="1", conta="388336", dia=hoje, resolvido=False),
                   db.Pendencia(gto="2", conta="hapvida:centro", dia=hoje, resolvido=False)])
        s.commit()
    assert db.dias_com_pendencia_aberta(7) == [("388336", hoje)]
    assert db.dias_com_pendencia_aberta(7, hapvida=True) == [("hapvida:centro", hoje)]


def test_retry_do_dia_nao_vale_para_hapvida(tmp_path, monkeypatch):
    _sqlite(tmp_path, monkeypatch, db.RetryFila)
    assert db.registrar_retry_dia("hapvida:centro", "01/09/2026", "x") is False
    with db.SessionLocal() as s:
        assert s.query(db.RetryFila).count() == 0


def test_rodada_cron_roda_cada_unidade_e_dia_e_grava(monkeypatch):
    monkeypatch.setenv("HAPVIDA_CONTAS", "centro,tancredo")
    monkeypatch.setenv("HAPVIDA_CRON_DIAS", "2")
    monkeypatch.setattr(db, "dias_com_pendencia_aberta", lambda p, hapvida=False: [])

    class Fonte:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    import hapvida_proradis
    monkeypatch.setattr(hapvida_proradis, "FonteProradis", Fonte)
    rodou, salvos, falhas = [], [], []

    def _rodar(dia, u, **kw):
        rodou.append((u, dia))
        if u == "tancredo" and dia == "24/09/2026":
            raise RuntimeError("login caiu")
        return {"conta": f"hapvida:{u}", "data": dia, "anexado_ok": 0, "pendentes": 1, "dry_run": True}
    monkeypatch.setattr(hapvida_app, "rodar_dia", _rodar)
    reservas = set()
    r = hapvida_app.rodada_cron(
        dt.date(2026, 9, 25),
        reservar=lambda d, c, t: (reservas.add((d, c)) or t),
        liberar=lambda d, c, t: reservas.discard((d, c)),
        salvar=lambda res, logs: salvos.append(res["conta"]),
        salvar_falha=lambda d, c, dry, e, logs: falhas.append((c, d, dry)),
        logger=logging.getLogger("t"))
    assert len(rodou) == 4 and r["execucoes"] == 3
    assert falhas == [("hapvida:tancredo", "24/09/2026", True)]   # sem envio real = dry
    assert reservas == set()                                      # tudo liberado


def _cliente(monkeypatch):
    import app as app_mod
    monkeypatch.setitem(app_mod.app.before_request_funcs, None, [])
    return app_mod, app_mod.app.test_client()


def test_tela_mostra_unidades_hapvida_como_simulacao(monkeypatch):
    monkeypatch.setenv("HAPVIDA_CONTAS", "centro")
    monkeypatch.delenv("HAPVIDA_ANEXAR_REAL", raising=False)
    app_mod, _ = _cliente(monkeypatch)
    ops = app_mod._opcoes_hapvida()
    assert ops == [{"codigo": "hapvida:centro", "label": "Hapvida Odonto — Centro — simulação"}]


def test_faturar_run_desvia_hapvida_e_recusa_unidade_desconhecida(monkeypatch):
    monkeypatch.setenv("HAPVIDA_CONTAS", "centro")
    app_mod, cli = _cliente(monkeypatch)
    r = cli.post("/faturar/run", data={"data": "23/09/2026", "plano": "hapvida:marte", "dry": "1"})
    assert r.status_code == 400 and "Hapvida" in r.get_json()["error"]

    chamou = []
    monkeypatch.setattr(hapvida_app, "rodar_dia", lambda d, u, **kw: chamou.append((d, u)) or
                        {"conta": "hapvida:centro", "data": d, "decisoes": [], "dry_run": True})
    monkeypatch.setattr(db, "salvar_execucao", lambda r, l: 7)
    monkeypatch.setattr(app_mod.threading, "Thread", lambda target, daemon: type(
        "T", (), {"start": lambda self: target()})())
    r = cli.post("/faturar/run", data={"data": "23/09/2026", "plano": "hapvida:centro", "dry": "1"})
    jid = r.get_json()["job"]
    assert chamou == [("23/09/2026", "centro")]
    assert app_mod._esteira_jobs[jid]["execucao_id"] == 7 and app_mod._esteira_jobs[jid]["done"]


def test_rotulo_da_unidade_hapvida():
    import notificador
    assert notificador._nome_unidade("hapvida:centro") == "Hapvida Odonto — Centro"


def test_confirmei_numa_pendencia_hapvida_roda_o_fluxo_hapvida(monkeypatch):
    app_mod, cli = _cliente(monkeypatch)
    monkeypatch.setitem(app_mod.app.before_request_funcs, None, [])

    class P:
        gto, conta, dia = "28547649", "hapvida:centro", "23/09/2026"

    class Sess:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, model, pid): return P()
    monkeypatch.setattr(db, "SessionLocal", lambda: Sess())
    monkeypatch.setattr(db, "confirmar_nome", lambda *a: True)
    monkeypatch.setattr(db, "salvar_execucao", lambda r, l: 1)
    chamou = []
    monkeypatch.setattr(hapvida_app, "rodar_dia", lambda d, u, **kw: chamou.append((d, u, kw["apenas_guias"])) or {})
    import esteira
    monkeypatch.setattr(esteira, "rodar_esteira", lambda *a, **k: (_ for _ in ()).throw(AssertionError("esteira!")))
    monkeypatch.setattr(app_mod.threading, "Thread", lambda target, daemon: type(
        "T", (), {"start": lambda self: target()})())
    with cli.session_transaction() as s:
        s["uid"] = 1
    r = cli.post("/pendencias/7/confirmar")
    assert r.status_code == 200 and r.get_json()["confirmado"]
    assert chamou == [("23/09/2026", "centro", ["28547649"])]
