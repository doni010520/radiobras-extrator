"""Pedido escrito 'periapical de boca completa' é levantamento (histórico Hapvida set/26:
ALEF, CAROLINA, JOSE VIRGILIO, MARIA DA GLORIA — a clínica anexou e a Hapvida pagou)."""
import pytest

from hapvida_pedido import diz_levantamento


@pytest.mark.parametrize("texto", [
    "RADIOGRAFIA PERIAPICAL DE BOCA COMPLETA",
    "radiografia periapical completa",
    "raio x periapical boca completa",
    "periapicais de boca completa",
    "levantamento periapical",
    "periapical de boca toda",
    "seriografia",
])
def test_diz_levantamento(texto):
    assert diz_levantamento({"exames_texto": texto, "texto": texto})


@pytest.mark.parametrize("texto", ["radiografia periapical", "periapical do 36", "radiografia panorâmica"])
def test_nao_e_levantamento(texto):
    assert not diz_levantamento({"exames_texto": texto, "texto": texto})
