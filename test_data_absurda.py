"""Data lida com ano impossivel nao e data (JOAO VITOR ROSEIRA DIAS, 197983509, 01/10).

"24/09/126" virava date(126, 9, 24): a janela de pareamento falhou por 1900 anos e o
pedido de periapical certo, de 01/10/2026, foi descartado. Com None, o fallback pela
data de upload (20261001 no nome do arquivo) decide.
"""
import datetime as dt

from esteira import _parse_br_date


def test_ano_de_tres_digitos_nao_vira_data():
    assert _parse_br_date("24/09/126") is None


def test_ano_no_futuro_distante_nao_vira_data():
    assert _parse_br_date("01/10/2206") is None


def test_datas_normais_continuam():
    assert _parse_br_date("01/10/2026") == dt.date(2026, 10, 1)
    assert _parse_br_date("01/10/26") == dt.date(2026, 10, 1)
    assert _parse_br_date("15/05/2025") == dt.date(2025, 5, 15)
