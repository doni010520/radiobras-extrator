"""A guia que JA TEM documento nao pode ser dada por faturada sem conferir se o
que esta la COBRE o que ela autoriza.

Caso LILIA CERQUEIRA FERREIRA (196485745, 21/08) e mais quatro de 21-22/08: o
laudo da panoramica estava PRONTO no PRORADIS desde o dia do exame, mas a guia so
tinha o do tracado. Como ela tinha documento, a descoberta respondia "ja tem
documentacao, pula" e marcava JA_ANEXADO -> anexado=OK -> faturada. O robo nunca
mais voltou nela: em 26, 27 e 28/08 o log repete a mesma linha de skip. Foram
buscados a mao em 28/08, no ultimo dia do prazo de 7 dias.

Aqui a leitura e TOLERANTE de proposito: o anexo colocado a mao tem nome livre
("Laudo Panoramico FULANO.pdf"), diferente do padrao do robo (LAUDO_<EXAME>_<acc>).
Falso positivo custa uma pendencia para conferir; falso negativo custa a guia."""
from esteira import _falta_no_portal


def test_guia_com_tracado_mas_sem_o_laudo_da_panoramica_nao_esta_completa():
    # LILIA: doc ortodontica exige panoramica E telerradiografia.
    anexos = ["ENTREGA_40cd541ac0.jpg", "ENTREGA_4bca93ff31.jpg",
              "LAUDO_TELERRADIOGRAFIA LATERAL_40344815_CEPH.pdf",
              "SOLICITACAO_0__LILIA_20260821_0002.pdf", "imagemGTO"]
    assert _falta_no_portal({"documentacao"}, anexos) == "laudo"


def test_guia_completa_segue_sendo_pulada():
    anexos = ["ENTREGA_a.jpg", "LAUDO_PANORAMICA_1_OFICIAL.pdf",
              "LAUDO_TELERRADIOGRAFIA_1_CEPH.pdf", "imagemGTO"]
    assert _falta_no_portal({"documentacao"}, anexos) == ""


def test_laudo_anexado_a_mao_com_nome_livre_conta():
    """Sem tolerancia a nome livre, toda guia resolvida pela clinica voltaria como
    incompleta — e a operacao perderia a confianca na fila."""
    anexos = ["image - 2026-08-22.jpg", "Laudo Panoramico ITALO.pdf", "imagemGTO"]
    assert _falta_no_portal({"panoramica"}, anexos) == ""


def test_guia_sem_nenhuma_imagem_acusa_imagem():
    # PALOMA (195670786): dois laudos, zero imagem -> GLOSADA 3230.
    anexos = ["LAUDO_PANORAMICA_1_OFICIAL.pdf", "LAUDO_TELERRADIOGRAFIA_1_CEPH.pdf",
              "SOLICITACAO_x.pdf", "imagemGTO"]
    assert _falta_no_portal({"documentacao"}, anexos) == "imagem"


def test_a_propria_GTO_assinada_nao_conta_como_imagem():
    assert _falta_no_portal({"panoramica"},
                            ["LAUDO_PANORAMICA_1_OFICIAL.pdf", "img_ASSINADA.png"]) == "imagem"


def test_sem_exames_de_referencia_nao_acusa_nada():
    """Guia cujo exame o portal nao devolveu: sem referencia nao ha o que cobrar."""
    assert _falta_no_portal(set(), ["ENTREGA_a.jpg", "imagemGTO"]) == ""
