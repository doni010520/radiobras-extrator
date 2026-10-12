"""Faturar sem unidade e recusado (regra do dono, 11/10)."""


def test_faturar_sem_unidade_e_recusado(monkeypatch):
    import app as app_mod
    monkeypatch.setitem(app_mod.app.before_request_funcs, None, [])
    r = app_mod.app.test_client().post("/faturar/run", data={"data": "01/10/2026", "dry": "0"})
    assert r.status_code == 400 and "unidade" in r.get_json()["error"]
