"""Gate do laudo por EXAME: uma guia que autoriza telerradiografia (direto ou via
documentacao ortodontica) NAO pode faturar so com a panoramica — precisa do laudo
da tele (CEPH/traçado). Causa dos 33 faturados-sem-tele (conferencia RedeUna 01/08).
Regra do dono: documentacao ortodontica SEMPRE inclui telerradiografia.

Falha SEGURA: na duvida exige o laudo (vira pendencia), nunca fatura incompleto."""
from esteira import _laudo_tele_faltando as falta


def test_doc_ortodontica_so_com_panoramica_exige_tele():
    # ANDRESSA/SOPHIA/etc: guia de doc orto, plano so tem a panoramica -> FALTA tele
    ex = {"documentacao"}
    plano = ["LAUDO_PANORAMICA_40338695_OFICIAL.pdf", "ENTREGA_1a2b3c4d.jpg",
             "SOLICITACAO_ANDRESSA.jpg"]
    assert falta(ex, plano) is True


def test_doc_ortodontica_com_tele_ceph_esta_ok():
    # RENATA: doc orto com panoramica + telerradiografia CEPH -> nao falta
    ex = {"documentacao"}
    plano = ["LAUDO_PANORAMICA_40338788_OFICIAL.pdf",
             "LAUDO_TELERRADIOGRAFIA_40338788_CEPH.pdf", "SOLICITACAO_RENATA.png"]
    assert falta(ex, plano) is False


def test_telerradiografia_avulsa_sem_laudo_exige():
    ex = {"panoramica", "telerradiografia"}
    plano = ["LAUDO_PANORAMICA_1_OFICIAL.pdf"]
    assert falta(ex, plano) is True


def test_telerradiografia_avulsa_com_laudo_ok():
    ex = {"telerradiografia"}
    plano = ["LAUDO_TELERRADIOGRAFIA_40339999_CEPH.pdf"]
    assert falta(ex, plano) is False


def test_guia_sem_tele_nao_exige_tele():
    # so panoramica -> nao autoriza tele -> nao pode virar pendencia por tele
    assert falta({"panoramica"}, ["LAUDO_PANORAMICA_1_OFICIAL.pdf"]) is False


def test_periapical_pura_nao_exige_tele():
    assert falta({"periapical"}, ["LAUDO_PERIAPICAL_1_OFICIAL.pdf"]) is False


def test_doc_ortodontica_e_periapical_com_pan_e_periapical_mas_sem_tele_exige():
    # SILVANILDES/MILENA: 'documentacao ortodontica e periapical', tem pan+peri, sem tele
    ex = {"documentacao", "periapical"}
    plano = ["LAUDO_PANORAMICA_1_OFICIAL.pdf", "LAUDO_PERIAPICAL_2_OFICIAL.pdf"]
    assert falta(ex, plano) is True


def test_plano_vazio_com_tele_autorizada_exige():
    assert falta({"documentacao"}, []) is True
