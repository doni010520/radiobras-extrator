"""Fase 3 — loop de retry do transitório: backoff + decisão de retentar (puros).
O loop só retenta 'transitorio', com backoff exponencial, até um teto; depois escala.
"""
from db import (retry_backoff_min, deve_retentar, MAX_RETRIES_TRANSITORIO,
                classe_efetiva)


def test_backoff_e_so_a_tentativa_imediata():
    """A escada longa ([0,0,5,20,60,240], teto 6) foi cortada em 29/08.

    Regra do dono (17/08) que FICA: a 2a tentativa e IMEDIATA — o que so depende de
    reprocessar nao espera 15min; resolve o 503 do Gemini de graca.

    O que SAIU: os degraus de 5min a 4h. Eles davam uma janela de ~5h30 por item e
    foi o que fez o domingo 23/08 bater de hora em hora ate 11h22 — cada tentativa
    com um login no OdontoPrev pelo proxy. Regra do dono (28/08): "nao ficar
    tentando varias vezes no mesmo dia; pode ser duas tentativas por dia, uma pela
    manha e uma pela tarde". O que nao resolve na imediata espera a proxima RODADA,
    que agora sao duas (ver test_duas_rodadas.py) e reprocessa o dia inteiro."""
    assert retry_backoff_min(0) == 0      # imediato
    assert retry_backoff_min(1) == 0      # 2a tentativa também imediata (seed=1)
    assert retry_backoff_min(2) == 0      # satura: nao ha mais degrau
    assert retry_backoff_min(9) == 0


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


def test_externo_e_conferencia_nunca_viram_esgotado():
    # esgotar so faz sentido pro que E retentavel. EXTERNO nunca foi: re-tentar nao
    # faz o radiologista emitir laudo. CONFERENCIA tambem nao: falta olho humano no
    # documento, nao rodada nova.
    assert classe_efetiva("falta o LAUDO do radiologista", "sem_laudo", tentativas=99) == "externo"
    assert classe_efetiva("a caligrafia do pedido está ilegível", "revisao",
                          tentativas=99) == "logica"


def test_logica_nossa_agora_esgota():
    """MUDANCA 22/08 (regra do dono): falha de sistema o sistema resolve com try
    again. nome_nao_bate deixou de ser 'logica parada esperando humano' e entrou no
    loop — logo ela TAMBEM pode esgotar o teto. Antes ficava 'logica' pra sempre e,
    pior, aparecia no painel do operador."""
    # (era 'nome_nao_bate'; em 23/08 ele virou CLINICA — documento de outra pessoa
    # nao se resolve re-tentando. Usa-se aqui outra logica NOSSA de verdade.)
    _NOSSA = "o robô não conseguiu ler quais exames a guia autoriza"
    assert classe_efetiva(_NOSSA, "guia", tentativas=0) == "logica"
    assert classe_efetiva(_NOSSA, "guia",
                          tentativas=MAX_RETRIES_TRANSITORIO) == "esgotado"
