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


# ── busca pelo NUMERO do prontuario (caso EMILI SANTOS DA SILVA, 197446404, 17/09) ──
# O cadastro existe (prontuario 20210405 = 'Cod. Pac' do analitico), mas a busca por
# nome nunca o devolve. Pelo numero, a tela traz o card dela e so ele.
def _fake_busca(monkeypatch, cards):
    import extrair_anexos_dia as ead
    termos = []
    monkeypatch.setattr(ead, "_buscar_na_tela", lambda page, t, c: termos.append(t) or {})
    monkeypatch.setattr(ead, "_cards_da_busca", lambda page: cards)
    return ead, termos


def test_codigo_exato_abre_o_prontuario(monkeypatch):
    ead, termos = _fake_busca(monkeypatch, [{"cod": "20210405", "href": "h-emili",
                                             "nome": "EMILI SANTOS DA SILVA"}])
    assert ead._buscar_por_codigo(None, "20210405") == "h-emili"
    assert termos == ["20210405"]


def test_card_unico_com_outro_numero_nao_e_aceito(monkeypatch):
    """O numero pode casar telefone/CPF de outra pessoa: card unico NAO basta."""
    ead, _ = _fake_busca(monkeypatch, [{"cod": "20999999", "href": "h-outra",
                                        "nome": "OUTRA PESSOA"}])
    assert ead._buscar_por_codigo(None, "20210405") is None


def test_numero_que_contem_o_codigo_nao_e_aceito(monkeypatch):
    ead, _ = _fake_busca(monkeypatch, [{"cod": "202104051", "href": "h-x", "nome": "X"}])
    assert ead._buscar_por_codigo(None, "20210405") is None


def test_codigo_sintetico_ou_vazio_nem_busca(monkeypatch):
    ead, termos = _fake_busca(monkeypatch, [])
    assert ead._buscar_por_codigo(None, "WL40352185") is None
    assert ead._buscar_por_codigo(None, "") is None
    assert termos == []


# ── campo de busca que nao carregou (caso LUIZ HENRIQUE, 197689615, 23/09) ──
class _PaginaSemCampo:
    def __init__(self, aparece_na=99):
        self.n = 0
        self.aparece_na = aparece_na

    def goto(self, *a, **k):
        self.n += 1

    def wait_for_timeout(self, ms):
        pass

    def wait_for_selector(self, *a, **k):
        if self.n < self.aparece_na:
            raise TimeoutError("nao apareceu")

    def query_selector(self, sel):
        return _Campo() if self.n >= self.aparece_na else None


class _Campo:
    def click(self):
        pass

    def fill(self, t):
        pass


def test_campo_que_nunca_carrega_vira_falha_tecnica_e_nao_nonetype():
    import pytest
    import extrair_anexos_dia as ead
    with pytest.raises(RuntimeError, match="falha técnica"):
        ead._buscar_na_tela(_PaginaSemCampo(), "FULANO", "1")


def test_campo_que_carrega_na_segunda_tentativa_segue(monkeypatch):
    import extrair_anexos_dia as ead
    monkeypatch.setattr(ead, "_record_href", lambda page, cod: {"href": "x", "n": 1})

    class _Kb:
        def press(self, k):
            pass
    p = _PaginaSemCampo(aparece_na=2)
    p.keyboard = _Kb()
    assert ead._buscar_na_tela(p, "FULANO", "1") == {"href": "x", "n": 1}


# ── homonimo no nome cheio: desempata NESSA tela (caso VIVIANE SANTOS SILVA, 07/10) ──
_VIVIANES = [
    {"href": "h-a", "nome": "VIVIANE SANTOS SILVA", "nascimento": "28/01/1996", "cod": "20210535"},
    {"href": "h-b", "nome": "VIVIANE SANTOS SILVA", "nascimento": "28/01/1996", "cod": "20133098"},
    {"href": "h-c", "nome": "VIVIANE SANTOS SILVA", "nascimento": "22/01/2023", "cod": "20055750"},
    {"href": "h-d", "nome": "VIVIANE SANTOS SILVA BANDEIRA", "nascimento": "21/06/1984", "cod": "20131279"},
]
_OUTRAS = [{"href": "h-x", "nome": "VIVIANE SANTOS DA SILVA", "nascimento": "04/07/1983", "cod": "1"}]


def test_homonimo_desempata_no_nome_cheio_e_une_o_cadastro_duplicado(monkeypatch):
    import extrair_anexos_dia as ead
    tela = {"cards": []}

    def busca(page, termo, cod):
        tela["cards"] = _VIVIANES if termo == "VIVIANE SANTOS SILVA" else _OUTRAS
        return {"href": None, "n": len(tela["cards"])}
    monkeypatch.setattr(ead, "_buscar_na_tela", busca)
    monkeypatch.setattr(ead, "_cards_da_busca", lambda page: tela["cards"])
    monkeypatch.setattr(ead, "_abrir_anexos",
                        lambda page, href, cod: [{"id": href, "filename": f"{href}.jpg"}])
    itens = ead.anexos_do_paciente(None, "VIVIANE SANTOS SILVA", "WL40357354", "1996-01-28")
    assert sorted(i["id"] for i in itens) == ["h-a", "h-b"]


def test_homonimo_sem_nascimento_continua_ambiguo(monkeypatch):
    import pytest
    import extrair_anexos_dia as ead
    monkeypatch.setattr(ead, "_buscar_na_tela", lambda p, t, c: {"href": None, "n": 4})
    monkeypatch.setattr(ead, "_cards_da_busca", lambda page: _VIVIANES)
    with pytest.raises(ead.ProntuarioAmbiguo):
        ead.anexos_do_paciente(None, "VIVIANE SANTOS SILVA", "WL40357354", "")
