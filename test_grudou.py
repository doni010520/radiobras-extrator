"""Confirmacao 'grudou' != 'enviei': depois do upload, cada arquivo enviado tem
que APARECER de fato nos anexos do portal (por IDENTIDADE _chave_anexo, robusto a
rotulo). Prova a causa raiz NILSON/RENATA: laudo aceito pelo POST mas ausente da
guia = faturado incompleto = glosa. _nao_grudaram devolve os que NAO persistiram."""
from extrator_odontoprev import _nao_grudaram


def test_laudo_enviado_mas_ausente_do_portal_e_flagado():
    # NILSON: mandamos o laudo, portal so mostra a copia da GTO -> laudo nao grudou
    portal = {"img_ASSINADA.png"}
    enviados = {"LAUDO_PANORAMICA_40312345_OFICIAL.pdf"}
    assert _nao_grudaram(portal, enviados) == ["LAUDO_PANORAMICA_40312345_OFICIAL.pdf"]


def test_laudo_presente_com_rotulo_de_exame_diferente_nao_e_flagado():
    # robustez: portal guardou o laudo com outro rotulo de exame (mesmo acc+TIPO)
    portal = {"LAUDO_INTERPROXIMAL_40312345_OFICIAL.pdf", "img_ASSINADA.png"}
    enviados = {"LAUDO_ATM_40312345_OFICIAL.pdf"}
    assert _nao_grudaram(portal, enviados) == []


def test_tudo_grudou_lista_vazia():
    portal = {"LAUDO_PANO_40399999_OFICIAL.pdf", "ENTREGA_a1b2c3d4.jpg",
              "SOLICITACAO_JOSE_123.pdf", "img_ASSINADA.png"}
    enviados = {"LAUDO_PANO_40399999_OFICIAL.pdf", "ENTREGA_a1b2c3d4.jpg",
                "SOLICITACAO_JOSE_123.pdf"}
    assert _nao_grudaram(portal, enviados) == []


def test_entrega_enviada_mas_ausente_e_flagada():
    portal = {"LAUDO_PANO_40399999_OFICIAL.pdf", "img_ASSINADA.png"}
    enviados = {"LAUDO_PANO_40399999_OFICIAL.pdf", "ENTREGA_a1b2c3d4.jpg"}
    assert _nao_grudaram(portal, enviados) == ["ENTREGA_a1b2c3d4.jpg"]


def test_portal_vazio_todos_enviados_faltaram():
    # (o chamador so age nisto quando a leitura foi REAL; aqui e a semantica pura)
    assert _nao_grudaram(set(), {"LAUDO_X_40311111_OFICIAL.pdf"}) == \
        ["LAUDO_X_40311111_OFICIAL.pdf"]


def test_cephalometrico_e_oficial_do_mesmo_acc_sao_distintos():
    # o portal so tem o OFICIAL; o CEPH enviado nao grudou -> flag so o CEPH
    portal = {"LAUDO_TELE_40355555_OFICIAL.pdf"}
    enviados = {"LAUDO_TELE_40355555_OFICIAL.pdf", "LAUDO_TELE_40355555_CEPH.pdf"}
    assert _nao_grudaram(portal, enviados) == ["LAUDO_TELE_40355555_CEPH.pdf"]
