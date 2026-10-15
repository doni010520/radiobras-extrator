"""Completar so guia que o ROBO faturou; a da clinica pela metade so vira pendencia (dono 15/10)."""
import completar


def test_guia_da_clinica_pela_metade_nao_e_completada(monkeypatch):
    monkeypatch.setattr(completar, "gtos_faturadas_pelo_robo", lambda g: {"111"})
    baixou = []
    monkeypatch.setattr(completar, "_baixar_do_proradis",
                        lambda alvos, dest, log: baixou.extend(a["gto"] for a in alvos) or {})
    monkeypatch.setattr(completar, "no_prazo", lambda d, hoje=None: True)

    class _PW:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    import playwright.sync_api as ps
    monkeypatch.setattr(ps, "sync_playwright", lambda: _PW())
    completar.rodada([{"gto": "111", "dia": "14/10/2026", "conta": "x"},
                      {"gto": "222", "dia": "14/10/2026", "conta": "x"}], log=lambda m: None)
    assert baixou == ["111"]
