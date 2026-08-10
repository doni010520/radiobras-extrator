"""Mapeamento de exames da guia (solicitacao_utils.canon_exames)."""
from solicitacao_utils import canon_exames


def test_tomo_computad_abreviado_do_portal_vira_tomografia():
    # OdontoPrev abrevia 'Tomografia Computadorizada' como 'Tomo Computad' no
    # evento do GTO (casos FRANCIS/ANDRE, cone-beam). Sem reconhecer, a guia
    # ficava 'ilegivel' (exame de referencia vazio) e nao faturava.
    assert canon_exames("Tomo Computad") == {"tomografia"}
    assert canon_exames("Tomo. Computadorizada") == {"tomografia"}


def test_tomografia_por_extenso_continua_valendo():
    assert canon_exames("Tomografia Computadorizada") == {"tomografia"}
    assert canon_exames("cone beam") == {"tomografia"}


def test_tomo_nao_over_matcha_dentro_de_outra_palavra():
    # 'tomo' colado em outra palavra (sem 'comput' depois) nao vira tomografia
    assert "tomografia" not in canon_exames("atomo de hidrogenio")
