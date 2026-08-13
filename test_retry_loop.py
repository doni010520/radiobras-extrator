"""Fase 3 — loop de retry do transitório: backoff + decisão de retentar (puros).
O loop só retenta 'transitorio', com backoff exponencial, até um teto; depois escala.
"""
from db import retry_backoff_min, deve_retentar, MAX_RETRIES_TRANSITORIO


def test_backoff_exponencial():
    # tentativa 0 (1a) -> 15min; dobra até 8h; satura em 8h
    assert retry_backoff_min(0) == 15
    assert retry_backoff_min(1) == 30
    assert retry_backoff_min(2) == 60
    assert retry_backoff_min(3) == 120
    assert retry_backoff_min(4) == 240
    assert retry_backoff_min(5) == 480
    assert retry_backoff_min(9) == 480   # satura, nunca passa de 8h


def test_so_transitorio_retenta():
    assert deve_retentar("transitorio", tentativas=0) is True
    assert deve_retentar("externo", tentativas=0) is False
    assert deve_retentar("logica", tentativas=0) is False


def test_estoura_o_teto_para_de_retentar():
    assert deve_retentar("transitorio", tentativas=MAX_RETRIES_TRANSITORIO - 1) is True
    assert deve_retentar("transitorio", tentativas=MAX_RETRIES_TRANSITORIO) is False
    assert deve_retentar("transitorio", tentativas=MAX_RETRIES_TRANSITORIO + 3) is False
