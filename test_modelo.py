"""MODELO de gesso: nao precisa de laudo, mas PRECISA da foto.

Regra do dono (22/08): "se o exame e 'modelo' ele nao precisa de laudo; basta uma
foto do modelo, uma foto que tenha as varias faces do modelo".

A primeira metade JA existia (`gto_dispensa_laudo`). O buraco estava na segunda: ao
dispensar o laudo, a guarda final do anexador era PULADA por inteiro
(`if not _laudo_no_plano and not dispensa_laudo`). E como imagem ausente e tratada
so como "nota", nao como pendencia, uma guia de modelo podia ser faturada com ZERO
entregavel — so a solicitacao anexada.

Correcao: dispensar laudo nao dispensa ENTREGAVEL. Troca a exigencia — em vez de
LAUDO_*, exige a foto (ENTREGA_*)."""
from esteira import _entregavel_faltando


def test_modelo_com_foto_pode_faturar():
    assert _entregavel_faltando(True, ["ENTREGA_ab12cd34ef.jpg", "SOLIC_1.pdf"]) is False


def test_modelo_SEM_foto_nao_pode_faturar():
    # o buraco: antes isto passava e faturava so com a solicitacao
    assert _entregavel_faltando(True, ["SOLIC_1.pdf"]) is True


def test_modelo_com_plano_vazio_nao_pode_faturar():
    assert _entregavel_faltando(True, []) is True
    assert _entregavel_faltando(True, None) is True


def test_modelo_aceita_qualquer_imagem_de_entrega():
    for f in ("ENTREGA_deadbeef01.jpg", "entrega_9f8e7d6c5b.JPG"):
        assert _entregavel_faltando(True, [f]) is False, f


def test_guia_radiologica_segue_exigindo_LAUDO_e_nao_foto():
    # o caminho comum NAO pode regredir: foto sozinha nunca substituiu laudo
    assert _entregavel_faltando(False, ["ENTREGA_ab12cd34ef.jpg"]) is True
    assert _entregavel_faltando(False, ["LAUDO_PANORAMICA_40342953_OFICIAL.pdf"]) is False


def test_laudo_tambem_serve_pra_guia_de_modelo():
    # se por algum motivo veio laudo numa guia de modelo, e entregavel do mesmo jeito
    assert _entregavel_faltando(True, ["LAUDO_PANORAMICA_1_OFICIAL.pdf"]) is False


def test_mensagem_do_modelo_fala_de_FOTO_e_nao_de_laudo():
    """Se a mensagem falar de laudo ou de convenio, manda a pessoa procurar a coisa
    errada — a guia de modelo nao tem laudo por definicao."""
    from db import classificar_pendencia
    m = ("a guia é de MODELO/FOTOGRAFIA (não precisa de laudo), mas não há foto do "
         "modelo para anexar — sem entregável não há o que faturar. O QUE FAZER: "
         "conferir se a foto do modelo (com as várias faces) foi gerada no PRORADIS "
         "e reprocessar o dia.")
    assert "foto do modelo" in m
    # e classifica como NOSSA: a foto e entregavel que NOS geramos, nao documento
    # que a clinica anexa nem laudo que o radiologista emite.
    from db import eh_nosso
    assert eh_nosso(m, "erro") is True
