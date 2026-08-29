"""Duas rodadas por dia (manha e tarde), decisao do dono em 28/08.

Uma rodada so as 5h significa que o laudo assinado as 10h da manha so e anexado as
5h do dia seguinte. Com o prazo de 7 dias da operadora e o dia-alvo em D-4, esse
dia perdido custa caro: seis guias de 20/08 perderam a janela por UM dia.

A segunda janela tambem substitui a escada longa de retry (que ia ate 4h de espera
e atravessava a manha): o que nao resolve na tentativa imediata espera a proxima
RODADA, que reprocessa o dia inteiro de qualquer forma."""
from datetime import datetime

from app import _slot_devido

HORAS = [5, 17]


def _dt(h, m=0):
    return datetime(2026, 8, 29, h, m)


def test_antes_da_primeira_janela_nao_roda():
    assert _slot_devido(_dt(4, 59), None, HORAS) is None


def test_primeira_janela_dispara():
    assert _slot_devido(_dt(5, 1), None, HORAS) == 5


def test_nao_repete_a_mesma_janela():
    assert _slot_devido(_dt(9), _dt(5, 8), HORAS) is None


def test_a_janela_da_tarde_dispara_mesmo_tendo_rodado_de_manha():
    assert _slot_devido(_dt(17, 2), _dt(5, 8), HORAS) == 17


def test_nao_repete_a_janela_da_tarde():
    assert _slot_devido(_dt(21), _dt(17, 40), HORAS) is None


def test_container_que_subiu_a_noite_roda_a_janela_mais_recente():
    """Reinicio as 20h com a ultima rodada em ONTEM: roda a das 17h, nao a das 5h."""
    assert _slot_devido(_dt(20), datetime(2026, 8, 28, 17, 5), HORAS) == 17


def test_uma_janela_so_continua_funcionando():
    assert _slot_devido(_dt(6), None, [5]) == 5
    assert _slot_devido(_dt(6), _dt(5, 2), [5]) is None
