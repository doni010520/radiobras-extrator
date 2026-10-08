"""Com homonimos, o robo abria SEMPRE o primeiro cartao da busca.

`_record_href` subia 6 niveis a partir do link "Prontuario" e testava
`innerText.includes(cod)`. No 6o nivel o no e a <ul> com TODOS os cartoes, entao o
primeiro cartao sempre "continha" o codigo. Reproduzido em 08/10: DANIELLE OLIVEIRA
SANTOS, prontuario 20210238 (nasc. 03/10/1992) -> abria 20099707 (nasc. 31/01/1990).
Guias afetadas: DANIELLE 197395684, ANA CRISTINA 197680014, SAMARA 197772101,
SIMONE 197983547, ANDREA 197109609 (leu pedidos de OUTRA Andrea), ELIANE 197340448.

Agora o cartao e casado pelo NUMERO EXATO (data-pat-id do <li>, ou o "Prontuario:"
do proprio cartao). Com codigo real, cartao unico que nao bate NAO e aceito.
"""
from extrair_anexos_dia import _escolher_card

DANIELLES = [
    {"href": "h-1990", "pat": "20099707", "num": "20099707"},
    {"href": "h-1992", "pat": "20210238", "num": "20210238"},
    {"href": "h-1996", "pat": "20008537", "num": "20008537"},
]


def test_homonimos_abre_o_cartao_do_codigo_e_nao_o_primeiro():
    assert _escolher_card(DANIELLES, "20210238") == {"href": "h-1992", "n": 3}


def test_sem_data_pat_id_usa_o_numero_do_proprio_cartao():
    cards = [dict(c, pat=None) for c in DANIELLES]
    assert _escolher_card(cards, "20008537")["href"] == "h-1996"


def test_codigo_que_nao_esta_em_nenhum_cartao_nao_abre_nada():
    assert _escolher_card(DANIELLES, "20555555")["href"] is None


def test_codigo_real_e_cartao_unico_de_outra_pessoa_nao_e_aceito():
    """Antes: cartao unico era aceito mesmo sem o codigo."""
    assert _escolher_card([DANIELLES[0]], "20210238")["href"] is None


def test_numero_que_so_contem_o_codigo_nao_casa():
    assert _escolher_card([{"href": "x", "pat": "202102381", "num": "202102381"}],
                          "20210238")["href"] is None


def test_codigo_sintetico_wl_mantem_o_aceite_de_cartao_unico():
    """Fallback por nome (cod 'WL*'): comportamento antigo preservado — cartao unico
    aceito, 2+ cartoes viram pendencia. As travas de nascimento seguem a jusante."""
    assert _escolher_card([DANIELLES[0]], "WL40352185")["href"] == "h-1990"
    assert _escolher_card(DANIELLES, "WL40352185")["href"] is None


def test_codigo_vazio_nao_casa_com_qualquer_cartao():
    assert _escolher_card(DANIELLES, "")["href"] is None
    assert _escolher_card([DANIELLES[1]], "")["href"] == "h-1992"
