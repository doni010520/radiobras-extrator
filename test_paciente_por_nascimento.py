"""Paciente cadastrado com OUTRA GRAFIA no PRORADIS: achar pela DATA DE NASCIMENTO.

O robo buscava o paciente SO pelo nome da guia. Em 13-16/09, das pendencias de
"paciente nao encontrado", a maioria existia no PRORADIS com grafia diferente:
  - VALDEMIR DOS ANJOS PEREIRA (guia)  x  VALDEMIR DOS SANTOS PEREIRA (cadastro)
  - EDNILDES RODRIGUES SOUZA           x  EDINILDS RODRIGUES SOARES
  - MARIA DA GLORIA JESUS MENDES       x  MARIA DA GLORIA DE JESUS MENDES
A guia traz a data de nascimento (/v1/gto/detalhada) e a busca de pacientes do
PRORADIS indexa nascimento (DD/MM/AAAA).

Regra VALIDADA EM MODO SOMBRA (16/09, 150 guias ja faturadas pelo nome, fingindo
que o nome falhou): 141 acertaram o MESMO cadastro, ZERO escolheram outra pessoa.
  - nascimento IDENTICO;
  - nome parecido NO TODO: conectivos (DE/DA/DOS...) ignorados, acento ignorado,
    PRIMEIRO nome igual ou erro de digitacao, >= 2 partes casando e no maximo UMA
    parte divergente de cada lado ("so o primeiro nome" e fragil — regra do dono);
  - entre candidatos, so vale quem tem EXAME na janela de datas; sobrou mais de
    uma pessoa (ou mais de um dia) -> ambiguo, nao chuta.
ADILSA DE SOUSA SANTOS (nao existe) continua sem candidato.
"""
import extrair_anexos_dia as ax

nc = ax._nome_casa_por_nascimento


# ── regra de nome ─────────────────────────────────────────────────────────────
def test_casos_reais_casam():
    assert nc("VALDEMIR DOS ANJOS PEREIRA", "VALDEMIR DOS SANTOS PEREIRA")
    assert nc("EDNILDES RODRIGUES SOUZA", "EDINILDS RODRIGUES SOARES")
    assert nc("MARIA DA GLORIA JESUS MENDES", "MARIA DA GLORIA DE JESUS MENDES")


def test_acento_e_espaco_nao_atrapalham():
    assert nc("JOSÉ  ANDRÉ FREITAS", "JOSE ANDRE FREITAS")


def test_primeiro_nome_diferente_e_outra_pessoa():
    """Irmaos/gemeos: mesmo nascimento, sobrenomes iguais, prenome diferente."""
    assert not nc("SAMILLE VIANA VALASQUES", "SAMARA VIANA VALASQUES")
    assert not nc("JOAO PEDRO SILVA SANTOS", "MARIA PEDRO SILVA SANTOS")


def test_duas_partes_divergentes_e_outra_pessoa():
    assert not nc("ANA PAULA SOUZA LIMA", "ANA CRISTINA ROCHA LIMA")


def test_so_o_primeiro_nome_em_comum_nao_basta():
    assert not nc("MARIA SILVA", "MARIA SANTOS")


def test_nome_de_uma_parte_so_nao_casa():
    assert not nc("MARIA", "MARIA")


# ── candidatos pelo nascimento ────────────────────────────────────────────────
def _c(nome, nasc, cod):
    return {"nome": nome, "nascimento": nasc, "cod": cod, "href": f"h/{cod}"}


def test_candidatos_filtram_nascimento_e_nome():
    cards = [_c("EDINILDS RODRIGUES SOARES", "16/11/1982", "20209592"),
             _c("DAISIANE MOURA DE JESUS", "16/11/1982", "1"),
             _c("EDINILDS RODRIGUES SOARES", "01/01/1990", "2")]
    r = ax._candidatos_por_nascimento(cards, "EDNILDES RODRIGUES SOUZA", "1982-11-16")
    assert [x["cod"] for x in r] == ["20209592"]


def test_sem_nascimento_na_guia_nao_ha_candidato():
    cards = [_c("EDINILDS RODRIGUES SOARES", "16/11/1982", "1")]
    assert ax._candidatos_por_nascimento(cards, "EDNILDES RODRIGUES SOUZA", "") == []


def test_parse_dos_cards_da_busca_http():
    html = """<div class="res">
      <div data-pat-id="20017877"><b>Perfil</b> VALDEMIR DOS SANTOS PEREIRA
        <span>Prontuário: 20017877</span> <span>Nascimento: 17/12/1976</span></div>
      <div data-pat-id="20205689"><a class="prontuario">Prontuário</a> VALDEMIR DOS ANJOS PEREIRA
        Prontuário: 20205689 Nascimento: 17/12/1976 Senha</div></div>"""
    cards = ax._cards_busca_http(html)
    assert {c["cod"]: (c["nome"], c["nascimento"]) for c in cards} == {
        "20017877": ("VALDEMIR DOS SANTOS PEREIRA", "17/12/1976"),
        "20205689": ("VALDEMIR DOS ANJOS PEREIRA", "17/12/1976")}


# ── desempate pelo exame na janela ────────────────────────────────────────────
esc = ax._escolher_candidato_com_exame


def test_valdemir_so_o_cadastro_com_exame_vale():
    """Dois cadastros com o mesmo nascimento; so 'DOS SANTOS' tem exame em 12/08."""
    r = esc({"VALDEMIR DOS ANJOS PEREIRA": {},
             "VALDEMIR DOS SANTOS PEREIRA": {"12/08/2026": ["40341825", "40341826"]}})
    assert r == ("VALDEMIR DOS SANTOS PEREIRA", "12/08/2026", ["40341825", "40341826"])


def test_duas_pessoas_com_exame_e_ambiguo():
    assert esc({"A B C": {"12/08/2026": ["1"]}, "A B D": {"12/08/2026": ["2"]}}) is None


def test_exame_em_dois_dias_e_ambiguo():
    assert esc({"A B C": {"12/08/2026": ["1"], "13/08/2026": ["2"]}}) is None


def test_ninguem_com_exame_nao_escolhe():
    assert esc({"A B C": {}, "A B D": {}}) is None


# ── fallback do prontuario (busca por nome achou 0 cards) ─────────────────────
def test_prontuario_por_nascimento_abre_pelo_codigo_do_cadastro():
    telas = []

    def tela(page, termo, cod):
        telas.append((termo, cod))
        return {"href": "https://x/record/20209864", "n": 1}
    cards = [_c("MARIA DA GLORIA DE JESUS MENDES", "24/11/1974", "20209864")]
    r = ax._prontuario_por_nascimento(None, "MARIA DA GLORIA JESUS MENDES", "1974-11-24",
                                      lambda p, n: cards, tela)
    assert r == ("https://x/record/20209864", "20209864")
    assert telas == [("MARIA DA GLORIA DE JESUS MENDES", "20209864")]


def test_prontuario_por_nascimento_dois_candidatos_nao_abre():
    cards = [_c("ANA B SILVA", "01/01/1990", "1"), _c("ANA B SOUZA", "01/01/1990", "2")]
    r = ax._prontuario_por_nascimento(None, "ANA B SANTOS", "1990-01-01",
                                      lambda p, n: cards, lambda *a: {"href": "h"})
    assert r is None


def test_prontuario_por_nascimento_cadastro_duplicado_mesma_pessoa_abre():
    cards = [_c("SAMILLE VIANA VALASQUES", "01/01/2000", "20209353"),
             _c("SAMILLE VIANA VALASQUES", "01/01/2000", "20209354")]
    r = ax._prontuario_por_nascimento(None, "SAMILE VIANA VALASQUES", "2000-01-01",
                                      lambda p, n: cards, lambda p, t, c: {"href": "h/" + c})
    assert r == ("h/20209353", "20209353")


# ── fallback da esteira (SEM_MATCH pelo nome) ─────────────────────────────────
from esteira import _achar_por_nascimento


def _wl(nome, acc):
    return {"nome": nome, "accession": acc, "rows_html": []}


def _fakes(cards, exames):
    """exames: {(nome, dia): [accessions]}"""
    chamadas = []

    def listar(pg, dia, nomes):
        chamadas.append((dia, nomes[0]))
        return [_wl(nomes[0], a) for a in exames.get((nomes[0], dia), [])] + \
               [_wl("OUTRA PESSOA QUALQUER", "999")]
    return (lambda pg, n: cards), listar, chamadas


def test_esteira_valdemir_acha_o_cadastro_com_exame_em_outro_dia():
    buscar, listar, _ = _fakes(
        [_c("VALDEMIR DOS SANTOS PEREIRA", "17/12/1976", "20017877"),
         _c("VALDEMIR DOS ANJOS PEREIRA", "17/12/1976", "20205689")],
        {("VALDEMIR DOS SANTOS PEREIRA", "12/08/2026"): ["40341825", "40341826"]})
    g = {"nome": "VALDEMIR DOS ANJOS PEREIRA", "nascimento": "1976-12-17"}
    r = _achar_por_nascimento(None, g, "11/08/2026", 7, buscar, listar)
    assert r["status"] == "OK"
    assert r["nome"] == "VALDEMIR DOS SANTOS PEREIRA"
    assert r["dia"] == "12/08/2026"
    assert r["accs"] == ["40341825", "40341826"]
    assert [w["accession"] for w in r["wl"]] == ["40341825", "40341826"]


def test_esteira_sem_nascimento_nem_busca():
    buscar, listar, chamadas = _fakes([], {})
    r = _achar_por_nascimento(None, {"nome": "X Y", "nascimento": ""}, "11/08/2026", 7,
                              buscar, listar)
    assert r["status"] == "NENHUM" and chamadas == []


def test_esteira_nome_incompativel_nao_vira_candidato():
    buscar, listar, chamadas = _fakes(
        [_c("DAISIANE MOURA DE JESUS", "16/11/1982", "1")],
        {("DAISIANE MOURA DE JESUS", "11/08/2026"): ["1"]})
    g = {"nome": "EDNILDES RODRIGUES SOUZA", "nascimento": "1982-11-16"}
    r = _achar_por_nascimento(None, g, "11/08/2026", 7, buscar, listar)
    assert r["status"] == "NENHUM" and chamadas == []


def test_esteira_duas_pessoas_com_exame_fica_ambiguo():
    buscar, listar, _ = _fakes(
        [_c("ANA B SILVA", "01/01/1990", "1"), _c("ANA B SOUZA", "01/01/1990", "2")],
        {("ANA B SILVA", "11/08/2026"): ["10"], ("ANA B SOUZA", "11/08/2026"): ["20"]})
    g = {"nome": "ANA B SANTOS", "nascimento": "1990-01-01"}
    r = _achar_por_nascimento(None, g, "11/08/2026", 7, buscar, listar)
    assert r["status"] == "AMBIGUO"


def test_esteira_candidato_sem_exame_na_janela_nao_fatura():
    buscar, listar, _ = _fakes([_c("EDINILDS RODRIGUES SOARES", "16/11/1982", "1")], {})
    g = {"nome": "EDNILDES RODRIGUES SOUZA", "nascimento": "1982-11-16"}
    r = _achar_por_nascimento(None, g, "11/08/2026", 2, buscar, listar)
    assert r["status"] == "NENHUM"
