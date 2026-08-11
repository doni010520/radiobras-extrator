"""Site-2: escolha SEGURA do prontuario no caminho WL — nascimento como trava dura
(_card_wl_por_nome_nascimento). Prova que resolve o MATEUS sem abrir prontuario de
outra pessoa (rejeita irmao, sobrenome diferente, nascimento diferente, ambiguo)."""
from extrair_anexos_dia import _card_wl_por_nome_nascimento as pick


def _c(nome, nasc, cod):
    return {"nome": nome, "nascimento": nasc, "cod": cod, "href": f"h/{cod}"}


def test_nome_do_meio_a_mais_com_nascimento_igual_casa():
    # MATEUS DA SILVA DE NOVAES (guia) x MATEUS DA SILVA MONTEIRO DE NOVAES (cadastro)
    cards = [_c("MATEUS DA SILVA MONTEIRO DE NOVAES", "10/05/2010", "111")]
    r = pick(cards, "MATEUS DA SILVA DE NOVAES", "10/05/2010")
    assert r and r["cod"] == "111"


def test_sem_nascimento_da_guia_nunca_casa():
    cards = [_c("MATEUS DA SILVA DE NOVAES", "10/05/2010", "111")]
    assert pick(cards, "MATEUS DA SILVA DE NOVAES", "") is None
    assert pick(cards, "MATEUS DA SILVA DE NOVAES", None) is None


def test_nascimento_diferente_rejeita_mesmo_com_nome_identico():
    cards = [_c("MATEUS DA SILVA DE NOVAES", "01/01/2000", "111")]
    assert pick(cards, "MATEUS DA SILVA DE NOVAES", "10/05/2010") is None


def test_irmao_mesmo_nascimento_rejeitado_pelo_nome():
    # adversarial: mesmo nascimento mas 1o nome diferente (irmao)
    cards = [_c("LUCAS DA SILVA DE NOVAES", "10/05/2010", "222")]
    assert pick(cards, "MATEUS DA SILVA DE NOVAES", "10/05/2010") is None


def test_sobrenome_final_diferente_rejeitado():
    cards = [_c("MATEUS DA SILVA DE COSTA", "10/05/2010", "333")]
    assert pick(cards, "MATEUS DA SILVA DE NOVAES", "10/05/2010") is None


def test_dois_com_mesmo_nome_e_nascimento_fica_ambiguo():
    cards = [_c("MATEUS DA SILVA DE NOVAES", "10/05/2010", "a"),
             _c("MATEUS DA SILVA MONTEIRO DE NOVAES", "10/05/2010", "b")]
    assert pick(cards, "MATEUS DA SILVA DE NOVAES", "10/05/2010") is None


def test_escolhe_o_certo_ignorando_homonimo_de_outra_data():
    cards = [_c("MATEUS DA SILVA MONTEIRO DE NOVAES", "10/05/2010", "certo"),
             _c("MATEUS DA SILVA DE NOVAES", "01/01/2000", "outro")]
    r = pick(cards, "MATEUS DA SILVA DE NOVAES", "10/05/2010")
    assert r and r["cod"] == "certo"
