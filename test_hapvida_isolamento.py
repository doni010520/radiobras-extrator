"""Hapvida e RedeUna não se cruzam no código compartilhado."""
import planos


def test_hapvida_registrado_inativo_com_handler_proprio():
    p = planos.get_plano("hapvida_odonto")
    assert p["handler"] == "hapvida" and p["ativo"] is False


def test_odontoprev_roda_na_esteira():
    """O pipeline antigo (fechar_dia, rota /fechar) saiu na Fase 2 (10/10): o plano
    da RedeUna roda na esteira (/faturar)."""
    p = planos.get_plano("odontoprev")
    assert p["handler"] == "esteira" and p["ativo"] is True


def test_rota_antiga_que_anexava_nao_existe_mais():
    import app as app_mod
    rotas = {r.rule for r in app_mod.app.url_map.iter_rules()}
    assert "/fechar" not in rotas and "/fechar-simples" not in rotas


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
