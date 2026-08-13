"""Gate de IMAGEM POR EXAME: cada exame radiologico autorizado precisa ter a imagem
entregavel (com logo) da SUA accession na guia. Falta -> pendencia (nao fatura).
Causa ALANA: periapical (acc 40340412) tinha 2 imagens cruas, 0 com logo -> a 'foto'
do periapical nao subiu, mas a esteira faturou assim mesmo.

Regra do dono: a imagem e por exame; o laudo e 1 doc que cobre todos; a tele entra
pelo tracado (ja tratado). 'captura' nao existe no sistema -> nao mexe aqui."""
from esteira import _acc_do_studyuid, _exame_precisa_imagem, exames_sem_imagem


def test_accession_sai_do_studyuid():
    assert _acc_do_studyuid("1.2.640.0.31017449.3.2.101.9.40340415.570874") == "40340415"
    assert _acc_do_studyuid("1.2.640.0.31017449.3.2.101.9.40340412.570872") == "40340412"


def test_studyuid_invalido_retorna_none():
    assert _acc_do_studyuid("") is None
    assert _acc_do_studyuid("lixo-sem-ponto") is None


def test_exames_radiologicos_precisam_de_imagem():
    for ex in ("panoramica", "periapical", "interproximal", "oclusal"):
        assert _exame_precisa_imagem(ex) is True, ex


def test_tele_documentacao_foto_modelo_nao_entram_aqui():
    # tele -> tracado (laudo, tratado noutro gate); documentacao -> bundle;
    # fotografia/modelo -> nao sao radiografia com accession propria
    for ex in ("telerradiografia", "documentacao", "documentacao_completa",
               "fotografia", "modelo"):
        assert _exame_precisa_imagem(ex) is False, ex


def test_periapical_sem_imagem_entregavel_e_flagado_ALANA():
    # ALANA: autoriza pan+periapical+tele; pan tem imagem, periapical NAO
    exames_com_acc = [("panoramica", "40340415"), ("periapical", "40340412")]
    accs_com_imagem = {"40340415"}   # so a panoramica gerou entregavel
    r = exames_sem_imagem(exames_com_acc, accs_com_imagem)
    assert r == [("periapical", "40340412")]


def test_tudo_com_imagem_nada_falta():
    exames_com_acc = [("panoramica", "40340415"), ("periapical", "40340412")]
    accs_com_imagem = {"40340415", "40340412"}
    assert exames_sem_imagem(exames_com_acc, accs_com_imagem) == []


def test_exame_sem_accession_nao_bloqueia():
    # sem accession nao da pra afirmar -> nao entra como falta (nao inventa pendencia)
    assert exames_sem_imagem([("panoramica", "")], set()) == []
    assert exames_sem_imagem([("periapical", None)], set()) == []
