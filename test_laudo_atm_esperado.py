"""Laudo de ATM e esperado: sem ele a guia nao fatura nem passa como completa (11/10)."""
from esteira import _documentacao_incompleta, _falta_no_portal

PLANO_MAIRA = ["ENTREGA_a.jpg", "LAUDO_PANORAMICA_40357627_OFICIAL.pdf",
               "LAUDO_TELERRADIOGRAFIA LATERAL_40357627_CEPH.pdf"]


def test_guia_com_atm_sem_laudo_de_atm_nao_fatura():
    # MAIRA (198256873): faturou com pan + tele; o laudo de ATM ainda nao tinha saido
    assert _documentacao_incompleta(False, {"atm", "documentacao", "periapical"},
                                    PLANO_MAIRA) == "laudo"


def test_guia_com_atm_e_laudo_de_atm_fatura():
    assert _documentacao_incompleta(False, {"atm", "documentacao", "periapical"},
                                    PLANO_MAIRA + ["LAUDO_ATM_40357631_OFICIAL.pdf"]) == ""


def test_guia_ja_anexada_sem_laudo_de_atm_e_reaberta():
    assert _falta_no_portal(["atm", "documentacao"], PLANO_MAIRA) == "laudo"


def test_laudo_de_atm_do_robo_e_reconhecido_no_portal():
    """LAUDO_ATM_<acc>: o '_' grudado escondia o ATM e a guia completa virava
    'falta laudo' (11/10)."""
    assert _falta_no_portal(["atm", "documentacao"],
                            PLANO_MAIRA + ["LAUDO_ATM_40357631_OFICIAL.pdf"]) == ""


def test_completar_sobe_o_laudo_de_atm_que_falta():
    from conferencia import arquivos_que_faltam
    assert arquivos_que_faltam(["atm", "documentacao"], PLANO_MAIRA,
                               ["LAUDO_ATM_40357631_OFICIAL.pdf", "ENTREGA_b.jpg"]) == [
        "LAUDO_ATM_40357631_OFICIAL.pdf"]
    assert arquivos_que_faltam(["atm", "documentacao"],
                               PLANO_MAIRA + ["LAUDO_ATM_40357631_OFICIAL.pdf"],
                               ["LAUDO_ATM_40357631_OFICIAL.pdf"]) == []
