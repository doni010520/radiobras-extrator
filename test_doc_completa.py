"""Documentação ortodôntica completa conferida por CONTEÚDO (18/10)."""
import os

import fitz
import pytest

import db
import doc_completa as dc


def _pdf(path, texto):
    d = fitz.open()
    d.new_page().insert_text((50, 72), texto)
    d.save(path)
    d.close()
    return path


@pytest.fixture
def pasta(tmp_path):
    for n in ("ENTREGA_a.jpg", "ENTREGA_b.jpg", "ENTREGA_c.jpg", "ENTREGA_d.jpg"):
        (tmp_path / n).write_bytes(b"\xff\xd8x")
    _pdf(str(tmp_path / "LAUDO_PANORAMICA_1_OFICIAL.pdf"), "Laudo panoramica")
    return tmp_path


def _arqs(p):
    return [str(p / f) for f in sorted(os.listdir(p))]


TUDO = {"panoramica", "telerradiografia", "foto_intra", "foto_extra"}


def test_so_vale_para_completa():
    assert dc.aplica(["documentacao", "documentacao_completa"])
    assert not dc.aplica(["documentacao", "documentacao_controle"])
    assert not dc.aplica(["panoramica"])
    assert dc.faltas(["panoramica"], [], lambda fs: 1 / 0) == []


def test_completa_passa(pasta):
    _pdf(str(pasta / "LAUDO_TELERRADIOGRAFIA_1_CEPH.pdf"), "Analise de Ricketts e USP")
    assert dc.faltas(["documentacao_completa"], _arqs(pasta), lambda fs: TUDO) == []


def test_sem_fotos_e_sem_analise(pasta):
    _pdf(str(pasta / "LAUDO_TELERRADIOGRAFIA_1_CEPH.pdf"), "Telerradiografia lateral sem tracado")
    f = dc.faltas(["documentacao_completa"], _arqs(pasta),
                  lambda fs: {"panoramica", "telerradiografia"})
    assert f == ["analise", "foto_intra", "foto_extra"]
    m = dc.mensagem(f)
    assert "fotos intrabucais" in m and "traçado" in m
    chave, quem = db.classificar_pendencia(m, "revisao")[:2]
    assert chave == "doc_incompleta" and quem == "Radiologista"
    assert not db.eh_nosso(m, "revisao")


def test_sem_folha_nenhuma_falta_tudo(tmp_path):
    f = dc.faltas(["documentacao_completa"], [], lambda fs: 1 / 0)
    assert f == list(dc.FOLHAS_EXIGIDAS)


def test_sem_laudo_de_tele_nao_duplica_regra(pasta):
    # sem CEPH, quem cobra é a regra 'esperando tele' — aqui não acusa análise
    assert dc.tem_analise(_arqs(pasta)) is None
    assert "analise" not in dc.faltas(["documentacao_completa"], _arqs(pasta), lambda fs: TUDO)


def test_falha_de_leitura_e_nossa():
    m = ("anexação falhou: a conferência da documentação ortodôntica não conseguiu ler as "
         "folhas (timeout) — o robô tenta de novo")
    assert db.eh_nosso(m, "revisao")
