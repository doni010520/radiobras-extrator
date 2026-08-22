"""ANALISES CEFALOMETRICAS: nao faturar tele com metade das analises laudadas.

Caso JOSEANE (15/08, GTO 196244053): o pedido dizia "Telerradiografia Rickets"; a
clinica so deixou pronto o laudo da analise USP. A trava de hoje pergunta "existe
ALGUM laudo de tele?" — existia, e a guia faturou faltando o Ricketts.

Verificado ao vivo no PRORADIS em 22/08: as duas analises NAO sao exames separados
(nao ha accession nem status proprio). Sao secoes DENTRO do mesmo laudo CEPH, e o
nome de cada uma aparece literalmente no texto: "Analise USP", "Analise de Ricketts".
O PDF renderizado tem camada de texto (fitz extrai 6.703 chars), entao a checagem le
o arquivo que ja esta na pasta — sem mexer no caminho de download.

REGRA DE PROJETO: so exige a analise que o pedido NOMEIA. A maioria dos pedidos diz
so "Telerradiografia", seco; exigir por padrao seguraria faturamento legitimo em
massa. Falso positivo aqui custa dinheiro do dono, nao da clinica."""
from solicitacao_utils import analises_pedidas, analises_no_texto, analises_faltando


# ── o que o PEDIDO nomeia ───────────────────────────────────────────────────
def test_pedido_da_joseane():
    assert analises_pedidas("Telerradiografia Rickets") == {"ricketts"}


def test_grafias_livres_do_dentista():
    # o dentista escreve a mao e abrevia; 'ricket' como prefixo cobre as tres
    for t in ("Telerradiografia Ricketts", "TELERRADIOGRAFIA RICKETES",
              "tele perfil c/ rickets"):
        assert analises_pedidas(t) == {"ricketts"}, t


def test_usp_e_tweed_no_mesmo_pedido():
    # caso MIRLA (22/07): "Teleradigrafia lateral com tweed e Usp"
    assert analises_pedidas("Teleradigrafia lateral com tweed e Usp") == {"tweed", "usp"}


def test_analise_cefalometrica_usp():
    assert analises_pedidas("Analise cefalometrica USP, Fotos extrabucais") == {"usp"}


def test_pedido_sem_analise_nomeada_nao_exige_nada():
    # o caso MAIS COMUM. Exigir aqui seguraria faturamento legitimo em massa.
    for t in ("Telerradiografia", "Rx Panoramico em topo",
              "documentacao ortodontica completa", ""):
        assert analises_pedidas(t) == set(), t


def test_usp_nao_casa_dentro_de_palavra():
    # 'USP' tem 3 letras e casaria dentro de qualquer coisa sem fronteira de palavra
    assert analises_pedidas("consultorio HOSPUSPA") == set()


def test_lista_de_exames_tambem_serve():
    # o que a esteira tem em maos e a lista `exames_lidos` do Gemini
    ex = ["Rx Panorâmico em topo", "Telerradiografia Rickets",
          "Fotografia extra/intra-bucal"]
    assert analises_pedidas(" ".join(ex)) == {"ricketts"}


# ── o que o LAUDO contem ────────────────────────────────────────────────────
_LAUDO_COMPLETO = ("Indicação: ALANA CAROLINE MULLER @radiologia.radiobras "
                   "Análise USP Fator Valor Obtido Padrão Desvio Classe "
                   "... Análise de Ricketts Cálculo de Vert: Braquifacial (0.67)")
_LAUDO_SO_USP = ("Indicação: ALANA CAROLINE MULLER Análise USP Fator Valor Obtido "
                 "Padrão Desvio Classe NAP: Perfil convexo")


def test_laudo_com_as_duas():
    assert analises_no_texto(_LAUDO_COMPLETO) == {"usp", "ricketts"}


def test_laudo_so_com_usp():
    assert analises_no_texto(_LAUDO_SO_USP) == {"usp"}


def test_texto_vazio():
    assert analises_no_texto("") == set()
    assert analises_no_texto(None) == set()


# ── a trava ─────────────────────────────────────────────────────────────────
def test_joseane_seria_segurada():
    """O caso real: pedido pede Ricketts, laudo so tem USP -> NAO fatura."""
    falta = analises_faltando(analises_pedidas("Telerradiografia Rickets"),
                              analises_no_texto(_LAUDO_SO_USP))
    assert falta == {"ricketts"}


def test_laudo_completo_libera():
    falta = analises_faltando(analises_pedidas("Telerradiografia Rickets"),
                              analises_no_texto(_LAUDO_COMPLETO))
    assert falta == set()


def test_pedido_sem_analise_libera_qualquer_laudo():
    # a regra de projeto: nao exige o que nao foi escrito
    assert analises_faltando(analises_pedidas("Telerradiografia"),
                             analises_no_texto(_LAUDO_SO_USP)) == set()


def test_laudo_ilegivel_nao_inventa_pendencia_de_terceiro():
    """Se nao conseguimos LER o laudo, o conjunto vem vazio. Isso nao pode virar
    'falta a analise' e mandar cobrar o radiologista — seria pendencia falsa. Quem
    chama decide; aqui a funcao so reporta a diferenca, e o gate na esteira exige
    ter conseguido ler alguma coisa antes de segurar."""
    assert analises_faltando({"ricketts"}, set()) == {"ricketts"}


# ── a mensagem tem que cair no GRUPO certo ─────────────────────────────────
# O texto contem "falta o LAUDO", que casa em `falta_laudo` — se o grupo novo nao
# vier ANTES na tabela, a pendencia vira uma cobranca generica de laudo e a clinica
# nao sabe QUAL analise pedir.
_MSG = ("NÃO FATUROU porque falta o LAUDO da análise Ricketts. A telerradiografia "
        "TEM laudo, mas o pedido nomeia essa análise e ela não está no documento — "
        "faturar assim entrega metade do que foi pedido. O robô anexa sozinho assim "
        "que a análise sair; cobrar a emissão.")


def test_mensagem_vira_esperando_analise_e_nao_falta_laudo():
    from db import classificar_pendencia
    chave, quem, _acao = classificar_pendencia(_MSG, "sem_laudo")
    assert chave == "esperando_analise"
    assert quem == "Radiologista"


def test_analise_faltando_NAO_e_falha_nossa():
    # o laudo existe; quem emite a analise e o radiologista. Marcar como nossa
    # esconderia do painel e ninguem cobraria a emissao.
    from db import eh_nosso, eh_pendencia_front, classe_retry
    assert eh_nosso(_MSG, "sem_laudo") is False
    assert eh_pendencia_front(_MSG, "sem_laudo") is True
    assert classe_retry(_MSG, "sem_laudo") == "externo"


def test_nao_conseguir_LER_o_laudo_e_falha_nossa():
    # o outro lado: se nao lemos o PDF, nao da pra cobrar ninguem. Vai pro retry.
    from db import eh_nosso, eh_pendencia_front
    m = ("NÃO FATUROU por falha técnica: o pedido nomeia uma análise cefalométrica e "
         "não consegui LER o laudo da telerradiografia para conferir se ela está lá. "
         "O QUE FAZER: reprocessar o dia. (Falha nossa — o laudo pode estar perfeito.)")
    assert eh_nosso(m, "erro") is True
    assert eh_pendencia_front(m, "erro") is False


def test_esperando_tele_nao_foi_atropelado():
    # o grupo novo entrou ANTES na tabela; o da tele nao pode ter sido roubado
    from db import classificar_pendencia
    m = "falta o LAUDO da telerradiografia (traçado cefalométrico)"
    assert classificar_pendencia(m, "sem_laudo")[0] == "esperando_tele"
