"""Exame autorizado e nao registrado no PRORADIS nao fatura (dono 11/10; CINTHIA 197492516)."""
from esteira import _exames_nao_registrados


def test_periapical_que_nao_existe_no_proradis():
    assert _exames_nao_registrados({"periapical"}, ["PANORAMICA"]) == {"periapical"}


def test_exame_registrado_mas_sem_laudo_nao_e_nao_feito():
    assert _exames_nao_registrados({"periapical"}, ["PANORAMICA", "PERIAPICAL BOCA COMPLETA"]) == set()


def test_levantamento_e_periapical_de_boca_toda_cobrem_interproximal():
    assert _exames_nao_registrados({"periapical", "interproximal"}, ["LEVANTAMENTO RADIOGRAFICO"]) == set()
    assert _exames_nao_registrados({"interproximal"}, ["PERIAPICAL BOCA COMPLETA"]) == set()


def test_sem_lista_do_proradis_nao_afirma_nada():
    assert _exames_nao_registrados({"periapical"}, []) == set()


def test_documentacao_cobre_componentes():
    assert _exames_nao_registrados({"panoramica", "telerradiografia"},
                                   ["DOCUMENTACAO ORTODONTICA COMPLETA"]) == set()
