"""JUNIOR / FILHO / NETO nao sao sobrenome — sao marcador de geracao.

Achado na varredura de leitura do PRORADIS em 23/08. A guia 196348961 e de

    HELIO DE SOUZA OLIVEIRA

e o exame registrado no PRORADIS (accessions 40343833/34/35, 18/08) esta em

    HELIO DE SOUZA OLIVEIRA JUNIOR

O matcher dava COMPATIVEL, porque a regra "menor totalmente contido no maior" trata
'JUNIOR' como um sobrenome a mais que o outro lado apenas nao trouxe. Mas ninguem
chamado "X JUNIOR" e a mesma pessoa que "X": sao pai e filho. E exatamente a classe
de erro do caso JOCASTA, com a diferenca de que aqui as duas pessoas dividem o nome
inteiro — o que torna o engano mais facil, nao menos.

Custo medido antes de apertar: 4135 itens ja faturados, ZERO com divergencia de
marcador geracional. Nenhuma guia que hoje fatura passa a falhar.

A regra so dispara quando o marcador esta de UM lado so. 'GILDASIO DOS SANTOS
OLIVEIRA SOBRINHO' contra ele mesmo continua casando."""
from esteira import _nomes_compat


# ── o caso ────────────────────────────────────────────────────────────────
def test_junior_nao_e_o_pai():
    assert _nomes_compat("HELIO DE SOUZA OLIVEIRA JUNIOR",
                         "HELIO DE SOUZA OLIVEIRA") is False


def test_pai_nao_e_o_junior():
    """Nos dois sentidos — o lado que traz o marcador nao importa."""
    assert _nomes_compat("HELIO DE SOUZA OLIVEIRA",
                         "HELIO DE SOUZA OLIVEIRA JUNIOR") is False


def test_filho_neto_sobrinho_tambem():
    for marca in ("FILHO", "NETO", "SOBRINHO", "JR"):
        assert _nomes_compat(f"JOSE DA SILVA {marca}", "JOSE DA SILVA") is False, marca


# ── nao pode virar recusa boba ────────────────────────────────────────────
def test_marcador_nos_DOIS_lados_continua_casando():
    assert _nomes_compat("GILDASIO DOS SANTOS OLIVEIRA SOBRINHO",
                         "GILDASIO DOS SANTOS OLIVEIRA SOBRINHO") is True


def test_marcador_nos_dois_lados_com_erro_de_grafia():
    assert _nomes_compat("Gildasio dos Santos Olivera Sobrinho",
                         "GILDASIO DOS SANTOS OLIVEIRA SOBRINHO") is True


def test_sem_marcador_nenhum_nada_muda():
    assert _nomes_compat("Priscila F. S. Dantas",
                         "PRISCILA FARIAS DOS SANTOS DANTAS") is True
    assert _nomes_compat("Sophia Carvallo do Rosamo",
                         "SOPHIA CARVALHO DO ROSARIO") is True


def test_nomes_que_contem_a_palavra_mas_nao_como_marcador():
    """'NETO' e 'JUNIOR' tambem existem como nome proprio/sobrenome comum. A regra
    olha o nome como token; se estiver dos dois lados, casa normalmente."""
    assert _nomes_compat("JUNIOR CESAR ALVES", "JUNIOR CESAR ALVES") is True
