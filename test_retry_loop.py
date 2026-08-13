"""Fase 3 — loop de retry do transitório: backoff + decisão de retentar (puros).
O loop só retenta 'transitorio', com backoff exponencial, até um teto; depois escala.
"""
from db import (retry_backoff_min, deve_retentar, MAX_RETRIES_TRANSITORIO,
                classe_efetiva)


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


# ── classe_efetiva: a etiqueta deixa de ser stateless (o furo que o dono achou) ──
# Um "transitorio" que ja falhou o teto de vezes NAO pode continuar se anunciando
# como "nosso, auto-recuperavel" — ele vira 'esgotado' (nossa, retry nao resolveu,
# investigar). Caso 195831154/195959119: "falha na leitura" repetida NAO recupera.
_LEITURA = "NÃO FATUROU por falha técnica na leitura dos documentos"


def test_transitorio_com_poucas_tentativas_continua_transitorio():
    assert classe_efetiva(_LEITURA, "erro", tentativas=0) == "transitorio"
    assert classe_efetiva(_LEITURA, "erro", tentativas=MAX_RETRIES_TRANSITORIO - 1) == "transitorio"


def test_transitorio_que_esgotou_o_teto_vira_esgotado():
    assert classe_efetiva(_LEITURA, "erro", tentativas=MAX_RETRIES_TRANSITORIO) == "esgotado"
    assert classe_efetiva(_LEITURA, "erro", tentativas=MAX_RETRIES_TRANSITORIO + 2) == "esgotado"


def test_externo_e_logica_nunca_viram_esgotado():
    # esgotar so faz sentido pro que ERA retentavel; externo/logica seguem iguais
    assert classe_efetiva("falta o LAUDO do radiologista", "sem_laudo", tentativas=99) == "externo"
    assert classe_efetiva("nenhum documento do prontuário está no nome", "sem_solicitacao",
                          tentativas=99) == "logica"
