"""So fatura com TODOS os laudos necessarios; um laudo pode cobrir varios exames
(regra do dono 11/10). O laudo da panoramica declara no titulo o que analisou."""
import fitz

from esteira import _exames_sem_laudo_conteudo


def _pdf(tmp_path, nome, titulo, corpo="Ausência do(s) dentes): 18. Recomendamos radiografia periapical para melhor análise."):
    p = tmp_path / nome
    d = fitz.open()
    pg = d.new_page()
    pg.insert_text((50, 80), "Paciente: MARIA DA SILVA")
    pg.insert_text((50, 100), "Solicitante: JOAO DENTISTA")
    pg.insert_text((50, 140), titulo)
    pg.insert_text((50, 170), corpo)
    d.save(str(p))
    return str(p)


def test_panoramica_que_declara_o_periapical_cobre(tmp_path):
    lau = _pdf(tmp_path, "LAUDO_PANORAMICA_1_OFICIAL.pdf",
               "Panorâmica da face, periapical boca completa e interproximais de molares")
    assert _exames_sem_laudo_conteudo({"panoramica", "periapical", "interproximal"}, [lau]) == set()


def test_cabecalho_radiografias_analisadas_cobre(tmp_path):
    lau = _pdf(tmp_path, "LAUDO_PANORAMICA_1_OFICIAL.pdf",
               "Radiografias Analisadas: Radiografia Panorâmica, Levantamento Radiográfico")
    assert _exames_sem_laudo_conteudo({"panoramica", "periapical"}, [lau]) == set()


def test_so_panoramica_nao_cobre_e_recomendacao_nao_conta(tmp_path):
    # DIEGO 197126440: titulo so "Radiografia Panoramica"; o corpo RECOMENDA periapical
    lau = _pdf(tmp_path, "LAUDO_PANORAMICA_1_OFICIAL.pdf", "Radiografia Panorâmica")
    assert _exames_sem_laudo_conteudo({"panoramica", "periapical"}, [lau]) == {"periapical"}


def test_laudo_proprio_do_periapical_cobre(tmp_path):
    pan = _pdf(tmp_path, "LAUDO_PANORAMICA_1_OFICIAL.pdf", "Radiografia Panorâmica")
    per = _pdf(tmp_path, "LAUDO_PERIAPICAL BOCA COMPLETA_2_OFICIAL.pdf", "Periapical boca completa")
    assert _exames_sem_laudo_conteudo({"panoramica", "periapical"}, [pan, per]) == set()


def test_modelo_e_foto_nao_pedem_laudo(tmp_path):
    pan = _pdf(tmp_path, "LAUDO_PANORAMICA_1_OFICIAL.pdf", "Radiografia Panorâmica")
    assert _exames_sem_laudo_conteudo({"panoramica", "modelo", "fotografia"}, [pan]) == set()


def test_controle_nao_pede_tele(tmp_path):
    pan = _pdf(tmp_path, "LAUDO_PANORAMICA_1_OFICIAL.pdf", "Radiografia Panorâmica")
    assert _exames_sem_laudo_conteudo({"documentacao", "documentacao_controle"}, [pan]) == set()
    assert _exames_sem_laudo_conteudo({"documentacao"}, [pan]) == {"telerradiografia"}
