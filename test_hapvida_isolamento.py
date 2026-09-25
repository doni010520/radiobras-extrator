"""Hapvida e RedeUna não se cruzam no código compartilhado."""
import planos


def test_hapvida_registrado_inativo_com_handler_proprio():
    p = planos.get_plano("hapvida_odonto")
    assert p["handler"] == "hapvida" and p["ativo"] is False


def test_odontoprev_continua_com_fechar_dia():
    p = planos.get_plano("odontoprev")
    assert p["handler"] == "fechar_dia" and p["ativo"] is True


def _cliente_sem_login(monkeypatch):
    import app as app_mod
    # o teste é da trava do /fechar, não do login: desliga só o before_request
    monkeypatch.setitem(app_mod.app.before_request_funcs, None, [])
    monkeypatch.setattr(app_mod.planos_mod, "plano_ativo", lambda slug: True)
    chamou = []
    monkeypatch.setattr(app_mod, "_run_fechar_job", lambda *a, **k: chamou.append(a))
    monkeypatch.setattr(app_mod.threading, "Thread", lambda target, args, daemon: type(
        "T", (), {"start": lambda self: target(*args)})())
    return app_mod.app.test_client(), chamou


def test_fechar_recusa_plano_de_outro_handler(monkeypatch):
    cli, chamou = _cliente_sem_login(monkeypatch)
    r = cli.post("/fechar", data={"data": "01/09/2026", "plano": "hapvida_odonto"})
    assert r.status_code == 400
    assert "fluxo próprio" in r.get_json()["error"]
    assert chamou == []


def test_fechar_continua_aceitando_odontoprev(monkeypatch):
    cli, chamou = _cliente_sem_login(monkeypatch)
    r = cli.post("/fechar", data={"data": "01/09/2026", "plano": "odontoprev", "simular": "1"})
    assert r.status_code == 200 and len(chamou) == 1


def test_retry_do_odontoprev_ignora_contas_hapvida(tmp_path, monkeypatch):
    from datetime import timedelta
    import db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    eng = create_engine(f"sqlite:///{tmp_path}/t.db")
    db.Base.metadata.create_all(eng, tables=[db.RetryFila.__table__])
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng))
    passado = db._now() - timedelta(minutes=5)
    with db.SessionLocal() as s:
        s.add_all([db.RetryFila(gto="1", conta="388336", dia="01/09/2026", proximo_em=passado, resolvido=False),
                   db.RetryFila(gto="2", conta="hapvida:centro", dia="01/09/2026", proximo_em=passado, resolvido=False)])
        s.commit()
    assert [d["conta"] for d in db.retries_devidos()] == ["388336"]
