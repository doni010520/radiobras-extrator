"""Regras de faturamento Hapvida — lógica pura. Dados sintéticos (repo é público)."""
import os

import pytest
from PIL import Image

import hapvida_decisao as hd

PANO = {"codigo": "81000405", "procedimento": "RADIOGRAFIA PANORAMI", "dente": "18/48"}
PERI = {"codigo": "81000421", "procedimento": "RADIOGRAFIA PERIAPIC", "dente": "24/25"}


def _guia(*itens, nome="MARIA TESTE SILVA"):
    return {"guia": "900001", "usuario": "X0000000000001", "nome": nome, "dia": "01/09/2026",
            "itens": list(itens)}


def _pr(**kw):
    base = {"encontrado": True, "ambiguo": False, "nome": "MARIA TESTE DA SILVA SOUZA",
            "nascimento": "01/02/1990", "entregaveis": ["ENTREGA_1.jpg"], "pedido": "pedido.png"}
    base.update(kw)
    return base


PAC = {"nome": "MARIA TESTE DA SILVA SOUZA", "nascimento": "01/02/1990"}


def test_agrupar_por_guia_preserva_ordem_dos_itens():
    linhas = [
        {"guia": "1", "usuario": "U", "nome": "A", "dt_atendimento": "08/09/2026 16:49", "codigo": "81000421"},
        {"guia": "1", "usuario": "U", "nome": "A", "dt_atendimento": "08/09/2026 16:50", "codigo": "81000375"},
        {"guia": "2", "usuario": "V", "nome": "B", "dt_atendimento": "08/09/2026 17:00", "codigo": "81000405"},
    ]
    g = hd.agrupar_por_guia(linhas)
    assert [i["codigo"] for i in g["1"]["itens"]] == ["81000421", "81000375"]
    assert g["1"]["dia"] == "08/09/2026" and g["2"]["usuario"] == "V"


def test_ja_anexada_nao_mexe():
    d = hd.decidir_guia(_guia(PANO), True, PAC, None)
    assert d.categoria == hd.JA_ANEXADA and not d.faturavel and d.anexar == []


def test_panoramica_completa_anexa_entregavel_e_pedido():
    d = hd.decidir_guia(_guia(PANO), False, PAC, _pr())
    assert d.faturavel and d.anexar == ["ENTREGA_1.jpg", "pedido.png"]


def test_panoramica_sem_pedido_vira_pendencia_da_clinica():
    d = hd.decidir_guia(_guia(PANO), False, PAC, _pr(pedido=None))
    assert d.categoria == hd.SEM_PEDIDO and d.responsavel == "Clínica" and not d.faturavel


def test_periapical_nao_exige_pedido():
    d = hd.decidir_guia(_guia(PERI), False, PAC, _pr(pedido=None))
    assert d.faturavel and d.anexar == ["ENTREGA_1.jpg"]


def test_periapical_leva_pedido_quando_existe():
    d = hd.decidir_guia(_guia(PERI), False, PAC, _pr())
    assert d.anexar == ["ENTREGA_1.jpg", "pedido.png"]


def test_sem_entregavel_e_do_radiologista():
    d = hd.decidir_guia(_guia(PANO), False, PAC, _pr(entregaveis=[]))
    assert d.categoria == hd.SEM_ENTREGAVEL and d.responsavel == "Radiologista"


def test_nao_achado_no_proradis_e_cadastro():
    d = hd.decidir_guia(_guia(PANO), False, PAC, {"encontrado": False})
    assert d.categoria == hd.NAO_ACHADO and d.responsavel == "Cadastro"


def test_ambiguo_nunca_anexa():
    d = hd.decidir_guia(_guia(PANO), False, PAC, _pr(ambiguo=True))
    assert not d.faturavel and d.responsavel == "Conferência"


def test_nascimento_divergente_barra_mesmo_com_nome_igual():
    d = hd.decidir_guia(_guia(PANO), False, PAC, _pr(nascimento="02/02/1990"))
    assert d.categoria == hd.IDENTIDADE and d.anexar == []


def test_nome_diferente_barra():
    d = hd.decidir_guia(_guia(PANO), False, PAC, _pr(nome="JOANA OUTRA PESSOA"))
    assert d.categoria == hd.IDENTIDADE


def test_codigo_fora_da_regra_vai_para_conferencia():
    d = hd.decidir_guia(_guia({"codigo": "85100013"}), False, PAC, _pr())
    assert not d.faturavel and d.responsavel == "Conferência"


@pytest.mark.parametrize("a,b,esperado", [
    ("JORGE PIMENTEL", "JORGE PIMENTEL DOS SANTOS", True),
    ("José da Silva", "JOSE DA SILVA", True),
    ("MARIA SOUZA", "ANA MARIA SOUZA", False),     # primeiro nome diferente
    ("CARLOS LIMA", "CARLOS SOUZA", False),
    ("", "CARLOS", False),
])
def test_nomes_compativeis(a, b, esperado):
    assert hd.nomes_compativeis(a, b) is esperado


def test_nascimento_aceita_formatos():
    ok, _ = hd.identidade_confere({"nome": "A B", "nascimento": "21/10/1969"},
                                  {"nome": "A B", "nascimento": "1969-10-21"})
    assert ok


def test_arquivo_pdf_nao_e_aceito(tmp_path):
    p = tmp_path / "laudo.pdf"; p.write_bytes(b"%PDF-1.4")
    ok, porque = hd.arquivo_aceito(str(p))
    assert not ok and "PDF".lower() in porque.lower()
    with pytest.raises(ValueError):
        hd.adequar_arquivo(str(p), str(tmp_path))


def test_jpg_pequeno_passa_intacto(tmp_path):
    p = tmp_path / "ENTREGA_1.jpg"
    Image.new("RGB", (400, 300), "white").save(p, "JPEG")
    assert hd.adequar_arquivo(str(p), str(tmp_path)) == str(p)


def test_png_grande_e_comprimido_abaixo_de_2mb(tmp_path):
    p = tmp_path / "ENTREGA_2.png"
    Image.frombytes("RGB", (2600, 2600), os.urandom(2600 * 2600 * 3)).save(p, "PNG")
    assert os.path.getsize(p) > hd.LIMITE_BYTES
    saida = hd.adequar_arquivo(str(p), str(tmp_path / ""))
    assert saida.endswith(".jpg") and os.path.getsize(saida) <= hd.LIMITE_BYTES
