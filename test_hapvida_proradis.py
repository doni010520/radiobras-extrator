"""Ponte Hapvida <-> PRORADIS: a saída do _baixa_um (caminho do RedeUna) vira a
entrada da decisão. Sem rede: pasta sintética no tmp."""
import pytest

import hapvida_decisao as hd
import hapvida_proradis as hp

PAN = ["81000405"]          # panorâmica (exige pedido)
PERI = ["81000421"]         # periapical (não exige)


def _pasta(tmp_path, *nomes):
    for n in nomes:
        (tmp_path / n).write_bytes(b"x")
    return str(tmp_path)


def _item(tmp_path, *nomes, **kw):
    it = {"status": "BAIXADO", "nome": "MARIA DA SILVA SANTOS",
          "_pasta": _pasta(tmp_path, *nomes), "extras_acc": [], "convenio_acc": []}
    it.update(kw)
    return it


def _guia(codigos):
    return {"guia": "1", "nome": "MARIA DA SILVA SANTOS", "dia": "08/09/2026",
            "itens": [{"codigo": c} for c in codigos]}


PAC = {"nome": "MARIA DA SILVA SANTOS", "nascimento": "01/02/1990"}


def test_exames_canon_cobre_levantamento_e_documentacao():
    assert hp.exames_canon(["81000294"]) == {"periapical", "interproximal"}
    assert "documentacao" in hp.exames_canon(["81000553"])
    assert hp.exames_canon(PAN) == {"panoramica"}


def test_ambiguo_e_sem_match_viram_pendencia_sem_chute():
    r = hp.resultado_da_busca({"status": "AMBIGUO", "nome": "X", "dias_com_exame": ["07/09/2026", "09/09/2026"]}, PERI)
    assert r["ambiguo"] and r["dias_com_exame"]
    assert hd.decidir_guia(_guia(PERI), False, PAC, r).categoria == hd.AMBIGUO
    r = hp.resultado_da_busca({"status": "SEM_MATCH"}, PERI)
    assert hd.decidir_guia(_guia(PERI), False, PAC, r).categoria == hd.NAO_ACHADO


def test_erro_de_download_sobe_como_falha_nossa():
    with pytest.raises(RuntimeError, match="folhas"):
        hp.resultado_da_busca({"status": "ERRO", "erro": "falha técnica: as folhas de imagem não carregaram"}, PERI)


def test_so_convenio_fatura_com_entregavel(tmp_path):
    it = _item(tmp_path, "ENTREGA_ab.jpg", "LAUDO_PERIAPICAL_111_OFICIAL.pdf", convenio_acc=["111"])
    r = hp.resultado_da_busca(it, PERI)
    assert not r["misto"] and len(r["entregaveis"]) == 1 and len(r["laudos"]) == 1
    d = hd.decidir_guia(_guia(PERI), False, PAC, r)
    assert d.faturavel and d.anexar == r["entregaveis"]


def test_exame_particular_no_mesmo_dia_nao_anexa(tmp_path):
    """accession fora do analítico HAPVIDA = particular/outro convênio. As folhas
    ENTREGA_ não dizem de qual exame são -> não anexa nada, vai para conferência."""
    it = _item(tmp_path, "ENTREGA_ab.jpg", "LAUDO_PERIAPICAL_111_OFICIAL.pdf",
               "LAUDO_PANORAMICA_222_OFICIAL.pdf", extras_acc=["222"], convenio_acc=["111"])
    r = hp.resultado_da_busca(it, PERI)
    assert r["misto"] and r["entregaveis"] == [] and "panoramica" in r["exames_fora"]
    d = hd.decidir_guia(_guia(PERI), False, PAC, r)
    assert not d.faturavel and d.responsavel == "Conferência" and "particular" in d.motivo


def test_data_do_exame_em_outro_dia_segue_para_o_fluxo(tmp_path):
    r = hp.resultado_da_busca(_item(tmp_path, "ENTREGA_ab.jpg", data_exame_real="10/09/2026"), PERI)
    assert r["data_exame_real"] == "10/09/2026"


def test_laudo_so_exigido_com_a_chave_ligada(tmp_path, monkeypatch):
    r = hp.resultado_da_busca(_item(tmp_path, "ENTREGA_ab.jpg"), PERI)
    monkeypatch.delenv("HAPVIDA_EXIGE_LAUDO", raising=False)
    assert hd.decidir_guia(_guia(PERI), False, PAC, r).faturavel
    monkeypatch.setenv("HAPVIDA_EXIGE_LAUDO", "1")
    d = hd.decidir_guia(_guia(PERI), False, PAC, r)
    assert not d.faturavel and d.responsavel == "Radiologista"
