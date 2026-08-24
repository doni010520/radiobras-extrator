"""Formulario que LISTA as analises nao esta PEDINDO todas elas.

Regra do dono: *"sobre USP + Ricketts a questao nao esta no Doc orto compl.. esta na
SOLICITACAO DO DENTISTA. e la que isso vai ser tratado como necessario ou nao."*

O gate le do pedido — certo. Mas varias clinicas usam formulario IMPRESSO com o
cardapio inteiro de servicos, e a IA transcreve o menu como se fosse o que foi
pedido. Medido na base em 23/08, logo depois de o gate voltar a funcionar:

    1 analise :  153 itens
    2 analises:   25 itens
    3 analises:   11 itens
    5 analises:    1 item
    6 analises:    8 itens   <- Jarabak + McNamara + Ricketts + Steiner + Tweed + USP

Nenhum dentista pede seis analises cefalometricas. O texto lido entrega o que e:
"Radiografias Intra-Bucais, Periapical Completa, Dente Unitario, Interproximais,
Oclusal, Radiografias Extra-Bucais..." — e o cardapio da clinica, nao a receita.

Com o gate vivo, esses 20 itens passariam a SEGURAR faturamento exigindo analises que
ninguem pediu. Seria o erro que o dono apontou, invertido: em vez de exigir por causa
da definicao de doc orto, exigir por causa do papel timbrado.

TETO EM 3. Um dentista marca uma analise, as vezes duas ("USP e Ricketts"). Tres ou
mais so aparece em lista de opcoes. E quando nao da para saber o que foi marcado, o
lado seguro e NAO exigir — e a mesma logica ja escrita no docstring de
`analises_pedidas`: falso positivo aqui custa dinheiro do dono, nao da clinica."""
from solicitacao_utils import analises_pedidas


_CATALOGO = ("Radiografias Intra-Bucais, Periapical Completa, Dente Unitario, "
             "Interproximais, Oclusal, Radiografias Extra-Bucais, Panoramica, "
             "Telerradiografia, Analise de Ricketts, Analise USP, Analise de "
             "Steiner, Analise de Tweed, Analise de Jarabak, Analise de McNamara")
_CATALOGO_5 = ("McNamara, Dows, Ricketts, Steiner, Bimler, Panoramica c/ tracado, "
               "Panoramica, Teleradiografia lateral")


# ── o caso ────────────────────────────────────────────────────────────────
def test_seis_analises_e_catalogo_nao_pedido():
    assert analises_pedidas(_CATALOGO) == set()


def test_cinco_analises_tambem():
    assert analises_pedidas(_CATALOGO_5) == set()


def test_tres_ja_e_catalogo():
    assert analises_pedidas("Analise Ricketts, Analise McNamara, Analise USP") == set()


# ── pedido de verdade continua exigindo ───────────────────────────────────
def test_uma_analise_e_pedido():
    r = analises_pedidas("Telerradiografia lateral com analise de Ricketts")
    assert "ricketts" in r


def test_duas_analises_e_pedido():
    """'USP e Ricketts' e o par que o dono citou — dentista pede os dois."""
    r = analises_pedidas("Telerradiografia com analise USP e Ricketts")
    assert r == {"usp", "ricketts"}


def test_pedido_sem_analise_continua_sem_exigir():
    assert analises_pedidas("Solicito telerradiografia lateral") == set()


def test_texto_vazio():
    assert analises_pedidas("") == set()
    assert analises_pedidas(None) == set()


# ── o LAUDO nao tem teto: um CEPH traz varias secoes de propósito ─────────
def test_laudo_com_seis_secoes_continua_lendo_todas():
    """`analises_no_texto` le o LAUDO, onde ter seis analises e NORMAL — o CEPH traz
    uma secao por analise. O teto vale so para o PEDIDO."""
    from solicitacao_utils import analises_no_texto
    r = analises_no_texto("Analise USP ... Analise de Ricketts ... Analise de Tweed "
                          "... Analise de Steiner ... Analise de Jarabak ... "
                          "Analise de McNamara")
    assert len(r) >= 5
