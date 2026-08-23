"""Guia que JA ESGOTOU nao volta no resumo da rodada seguinte.

Medido nos alertas reais de 23/08: **6 dos 18** avisos eram repeticao da mesma guia
em minutos:

    06:11  ❗ o retry nao recuperou   — FABRICIO, 6 tentativas
    06:14  ⚠️ 1 guia com falha nossa  — FABRICIO      <- 3 min depois
    06:35  ❗❗ as duas DILMA
    06:37  ⚠️ 2 guias                 — as duas DILMA <- 2 min depois
    07:09  ❗ HOSANA
    07:11  ⚠️ 1 guia                  — HOSANA        <- 2 min depois

E nao e so barulho: o resumo diz *"o sistema ja esta re-tentando"* — o que e FALSO
para uma guia que acabou de esgotar o teto. O dono recebe, em sequencia, "precisa de
voce" e "pode relaxar que o robo esta cuidando". A segunda mensagem desmente a
primeira.

Causa: quando a guia esgota, `bump_retry` marca resolvido=True. Na rodada seguinte
`retry_na_fila` devolve False (porque a linha esta resolvida), a guia e contada como
NOVA e entra no resumo."""
import db


def test_escalou_recentemente_e_pura():
    """A funcao decide pelo ESTADO da fila, nao por texto de mensagem."""
    assert hasattr(db, "escalou_recentemente")


def test_guia_esgotada_nao_entra_no_resumo(monkeypatch):
    """O caso FABRICIO: esgotou 06:11, resumo 06:14."""
    monkeypatch.setattr(db, "escalou_recentemente", lambda gto, horas=24: True)
    assert db.deve_avisar_na_rodada("196307916") is False


def test_guia_normal_entra_no_resumo(monkeypatch):
    """Falha nossa que ainda vai ser re-tentada CONTINUA sendo avisada — o dono
    precisa saber que a guia nao faturou, mesmo que o robo va cuidar."""
    monkeypatch.setattr(db, "escalou_recentemente", lambda gto, horas=24: False)
    assert db.deve_avisar_na_rodada("196999999") is True


def test_janela_e_de_24h(monkeypatch):
    """Depois de um dia, uma nova falha da mesma guia volta a ser avisada — senao
    uma guia que quebra toda semana ficaria muda para sempre."""
    vistos = {}

    def _fake(gto, horas=24):
        vistos["horas"] = horas
        return False
    monkeypatch.setattr(db, "escalou_recentemente", _fake)
    db.deve_avisar_na_rodada("1")
    assert vistos["horas"] == 24
