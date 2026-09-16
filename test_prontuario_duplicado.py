"""Prontuario DUPLICADO do mesmo paciente nao e homonimo.

Caso SAMILLE VIANA VALASQUES (GTO 197138342, Camacari, 09/09): o PRORADIS tem DOIS
cadastros — 20209353 (com o atendimento de 09/09) e 20209354 (vazio) — com o MESMO
nome e o MESMO nascimento (15/12/1997, igual ao da guia). O desempate por
nascimento so aceitava se sobrasse UM card; sobravam dois, e a guia morria em
"2 paciente(s) com o nome 'SAMILLE VIANA VALASQUES' no PRORADIS" por 8 rodadas,
ate vencer. Nenhum re-run resolve: os dois empatam em tudo.

Mesmo nome normalizado + mesmo nascimento = a MESMA pessoa cadastrada duas vezes
(caso IRAMAIA, que `_gemeos_de` ja trata unindo os anexos dos dois prontuarios).
Escolher um card e deixar `_gemeos_de` juntar os anexos e seguro.

Continua AMBIGUO (nao escolhe): nomes diferentes com o mesmo nascimento — gemeos de
verdade, ou cadastro com nome do meio a mais junto de outro sem (test_site2).
"""
import extrair_anexos_dia as ax


def _c(nome, nasc, cod):
    return {"nome": nome, "nascimento": nasc, "cod": cod, "href": f"h/{cod}"}


SAMILLE = [_c("SAMILLE VIANA VALASQUES", "15/12/1997", "20209353"),
           _c("SAMILLE VIANA VALASQUES", "15/12/1997", "20209354")]


# ── o reconhecimento da duplicata ─────────────────────────────────────────────
def test_mesmo_nome_e_mesmo_nascimento_e_a_mesma_pessoa():
    r = ax._duplicata_mesma_pessoa(SAMILLE)
    assert r and r["cod"] in ("20209353", "20209354")


def test_diferenca_so_de_acento_e_espaco_ainda_e_a_mesma_pessoa():
    cards = [_c("SAMILLE VIANA VALASQUES", "15/12/1997", "1"),
             _c("Samille  Viána Valasques", "15/12/1997", "2")]
    assert ax._duplicata_mesma_pessoa(cards)


def test_gemeos_de_verdade_nao_sao_duplicata():
    """Mesmo nascimento, prenome diferente: outra pessoa."""
    cards = [_c("SAMILLE VIANA VALASQUES", "15/12/1997", "1"),
             _c("SAMARA VIANA VALASQUES", "15/12/1997", "2")]
    assert ax._duplicata_mesma_pessoa(cards) is None


def test_mesmo_nome_com_nascimento_diferente_nao_e_duplicata():
    cards = [_c("SAMILLE VIANA VALASQUES", "15/12/1997", "1"),
             _c("SAMILLE VIANA VALASQUES", "02/03/1980", "2")]
    assert ax._duplicata_mesma_pessoa(cards) is None


def test_card_sem_nascimento_nunca_prova_duplicata():
    cards = [_c("SAMILLE VIANA VALASQUES", "", "1"),
             _c("SAMILLE VIANA VALASQUES", "", "2")]
    assert ax._duplicata_mesma_pessoa(cards) is None


def test_um_card_so_nao_e_duplicata():
    assert ax._duplicata_mesma_pessoa(SAMILLE[:1]) is None
    assert ax._duplicata_mesma_pessoa([]) is None


# ── site-1: desempate por nascimento ─────────────────────────────────────────
def test_site1_escolhe_quando_o_empate_e_a_mesma_pessoa_duplicada():
    r = ax._card_por_nascimento(SAMILLE, "1997-12-15")
    assert r and r["cod"] in ("20209353", "20209354")


def test_site1_continua_escolhendo_o_unico_que_bate():
    cards = [_c("SAMILLE VIANA VALASQUES", "15/12/1997", "certo"),
             _c("SAMILLE VIANA VALASQUES", "01/01/2001", "outro")]
    assert ax._card_por_nascimento(cards, "1997-12-15")["cod"] == "certo"


def test_site1_continua_ambiguo_para_pessoas_diferentes():
    cards = [_c("FELIPE SILVA DOS SANTOS", "15/12/1997", "a"),
             _c("FELIPE SILVA SANTOS", "15/12/1997", "b")]
    assert ax._card_por_nascimento(cards, "1997-12-15") is None


def test_site1_sem_nascimento_na_guia_nao_inventa():
    assert ax._card_por_nascimento(SAMILLE, "") is None


# ── site-2 (caminho WL) ──────────────────────────────────────────────────────
def test_site2_aceita_a_duplicata_da_mesma_pessoa():
    r = ax._card_wl_por_nome_nascimento(SAMILLE, "SAMILLE VIANA VALASQUES", "1997-12-15")
    assert r and r["cod"] in ("20209353", "20209354")
