"""Encurtar a busca do prontuario nao pode virar uma busca inutil.

Caso MARIA DE FATIMA LAMOEDO (GTO 196370003, 388336, 19/08) — a ultima das quatro
pendencias tecnicas, e a unica que sobrou como NOSSA depois das correcoes de 23/08.
Sete reprocessamentos, sempre o mesmo erro:

    24 pacientes com o nome 'MARIA DE FATIMA LAMOEDO' no PRORADIS —
    nao foi possivel identificar o prontuario com seguranca

Dois defeitos, e o primeiro faz a mensagem MENTIR.

1. A busca tenta o nome cheio e, falhando, vai encurtando pelo prefixo. `n_cards` e
   reatribuido a cada tentativa, entao o numero que sobra no fim e o da ULTIMA busca
   — a mais curta e mais ampla. A mensagem entao anuncia esse numero ao lado do nome
   COMPLETO. Nao existem 24 pacientes chamados 'MARIA DE FATIMA LAMOEDO': os 24 sao
   resultado de 'MARIA DE'. A operadora e mandada procurar homonimo que nao existe.

2. O prefixo desce ate 2 tokens SEM olhar o que os tokens sao. Para este nome isso
   produz 'MARIA DE' — um prenome comunissimo mais uma preposicao. A busca satura
   por construcao, e o resultado saturado e o que derruba a guia.

O conserto de (2): um termo encurtado precisa manter 2 tokens SIGNIFICATIVOS
(preposicao nao conta). 'ANGELICA OLIVEIRA LEAHY' -> 'ANGELICA OLIVEIRA' continua
valendo (caso 27/07); 'MARIA DE FATIMA LAMOEDO' -> 'MARIA DE' deixa de ser tentada."""
from extrair_anexos_dia import _termos_de_busca


# ── o caso ────────────────────────────────────────────────────────────────
def test_lamoedo_nao_busca_maria_de():
    t = _termos_de_busca("MARIA DE FATIMA LAMOEDO", cod_s="12345")
    assert t[0] == "MARIA DE FATIMA LAMOEDO"
    assert "MARIA DE" not in t, "prefixo de 1 nome + preposicao satura a busca"
    assert "MARIA DE FATIMA" in t


def test_encurtamento_util_continua():
    """Caso ANGELICA OLIVEIRA LEAHY (27/07) — tirar o ultimo sobrenome resolve."""
    t = _termos_de_busca("ANGELICA OLIVEIRA LEAHY", cod_s="12345")
    assert "ANGELICA OLIVEIRA" in t


def test_nome_de_dois_tokens_nao_encurta():
    t = _termos_de_busca("JOAO SILVA", cod_s="12345")
    assert t == ["JOAO SILVA"]


def test_preposicoes_nao_contam_como_token():
    t = _termos_de_busca("JOSE DOS SANTOS LIMA", cod_s="12345")
    assert "JOSE DOS" not in t
    assert "JOSE DOS SANTOS" in t


# ── as travas de seguranca que ja existiam ────────────────────────────────
def test_sem_codigo_real_nao_encurta():
    """Com cod vazio, o card 'contem o codigo' e verdadeiro para QUALQUER card:
    busca mais larga abriria prontuario de outra pessoa (code review 31/07)."""
    assert _termos_de_busca("MARIA DE FATIMA LAMOEDO", cod_s="") == \
        ["MARIA DE FATIMA LAMOEDO"]


def test_wl_sem_nascimento_nao_encurta():
    assert _termos_de_busca("MARIA DE FATIMA LAMOEDO", cod_s="WL123") == \
        ["MARIA DE FATIMA LAMOEDO"]


def test_wl_com_nascimento_encurta():
    """Site-2: a aceitacao exige nascimento igual + card unico (caso MATEUS 05/08)."""
    t = _termos_de_busca("MARIA DE FATIMA LAMOEDO", cod_s="WL123", tem_nascimento=True)
    assert "MARIA DE FATIMA" in t
    assert "MARIA DE" not in t


def test_espaco_duplo_ja_vem_colapsado():
    t = _termos_de_busca("ANGELICA OLIVEIRA LEAHY", cod_s="1")
    assert all("  " not in x for x in t)


def test_ordem_do_mais_especifico_para_o_mais_amplo():
    t = _termos_de_busca("ANA PAULA SOUZA COSTA", cod_s="1")
    assert t == ["ANA PAULA SOUZA COSTA", "ANA PAULA SOUZA", "ANA PAULA"]
