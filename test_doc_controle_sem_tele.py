"""Documentacao de CONTROLE = foto + panoramica, sem tele (dono 28-30/07; dados 11/10).
JACI 197119716 e CAMILA 197061105 ficaram presas esperando um tracado que nao existe."""
from solicitacao_utils import canon_exames, lista_amigavel
from esteira import _documentacao_incompleta, _falta_no_portal, _laudo_tele_faltando
from conferencia import arquivos_que_faltam

PAN = ["ENTREGA_a.jpg", "LAUDO_PANORAMICA_1_OFICIAL.pdf"]


def test_controle_fatura_so_com_panoramica():
    ctl = canon_exames("Doc Orto Contro")
    assert "documentacao_controle" in ctl
    assert not _laudo_tele_faltando(ctl, PAN)
    assert _documentacao_incompleta(False, ctl, PAN) == ""
    assert _falta_no_portal(ctl, PAN) == ""


def test_controle_sem_panoramica_continua_pendente():
    ctl = canon_exames("DOCUMENTAÇÃO CONTROLE")
    assert _documentacao_incompleta(False, ctl, ["ENTREGA_a.jpg"]) != ""


def test_completa_e_basica_continuam_exigindo_tele():
    for t in ("Doc Orto Compl", "Doc Orto Basica", "documentação ortodôntica"):
        assert _laudo_tele_faltando(canon_exames(t), PAN), t


def test_subtipo_sobrevive_ao_texto_da_guia():
    # a conferencia pos-rodada re-le o texto gravado (exames_gto)
    txt = lista_amigavel(canon_exames("Doc Orto Contro"))
    assert "controle" in txt and "documentacao_controle" in canon_exames(txt)
    assert arquivos_que_faltam(canon_exames(txt), ["ENTREGA_a.jpg"],
                               ["LAUDO_PANORAMICA_1_OFICIAL.pdf"]) == ["LAUDO_PANORAMICA_1_OFICIAL.pdf"]
