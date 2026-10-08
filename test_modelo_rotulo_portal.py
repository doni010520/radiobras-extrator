"""Guia de MODELO puro nunca faturava (caso NICOLAS RAMOS FANELI, 197564266, 21/09).

O portal OdontoPrev escreve o procedimento abreviado: 'Model.Ortodon.' e
'Rad.Pan+Mod.Ort'. O _CANON so conhecia a palavra 'modelo' inteira, entao a guia
chegava com exame VAZIO ("nenhum"), a regra "modelo dispensa laudo" nunca ligava e
o robo pedia laudo + imagem com logo de um exame que nao tem nenhum dos dois. As 22
guias de modelo de 20/07 a 08/10 acabaram anexadas a mao pela operadora.
"""
from solicitacao_utils import canon_exames
from esteira import _portal_dispensa_laudo


def test_model_ortodon_do_portal_e_modelo():
    assert canon_exames("Model.Ortodon.") == {"modelo"}


def test_rad_pan_mais_mod_ort_e_panoramica_e_modelo():
    assert canon_exames("Rad.Pan+Mod.Ort") == {"panoramica", "modelo"}


def test_rotulos_que_ja_funcionavam_nao_mudam():
    assert canon_exames("Rad.Panor.S/Tra") == {"panoramica"}
    assert canon_exames("Rad.Pano.C/Trac") == {"panoramica"}
    assert canon_exames("Doc Orto Compl") == {"documentacao", "documentacao_completa"}
    assert canon_exames("Telerrad.C/Trac") == {"telerradiografia"}
    assert canon_exames("Tomo Computad") == {"tomografia"}
    assert canon_exames("MODELO") == {"modelo"}


def test_palavras_parecidas_nao_viram_modelo():
    assert "modelo" not in canon_exames("modelagem do sorriso")
    assert "modelo" not in canon_exames("modo de uso")
    assert "panoramica" not in canon_exames("radiografia panamenha")  # 'pan' sem 'rad'


def test_portal_so_modelo_dispensa_laudo():
    assert _portal_dispensa_laudo(["Model.Ortodon."]) is True


def test_portal_modelo_mais_fotografia_dispensa_laudo():
    assert _portal_dispensa_laudo(["Model.Ortodon.", "Fotografia"]) is True


def test_portal_com_radiografia_nao_dispensa():
    assert _portal_dispensa_laudo(["Rad.Pan+Mod.Ort"]) is False
    assert _portal_dispensa_laudo(["Model.Ortodon.", "Rad.Panor.S/Tra"]) is False


def test_portal_vazio_ou_desconhecido_nao_dispensa():
    assert _portal_dispensa_laudo([]) is False
    assert _portal_dispensa_laudo(None) is False
    assert _portal_dispensa_laudo(["Procedimento X"]) is False


# ── "radiografia de perfil" e telerradiografia (JOAO VICTOR 197151270, DAIANE
# 197130946, 09/09: pedidos impressos e legiveis recusados) ──────────────────────
def test_radiografia_de_perfil_e_telerradiografia():
    assert "telerradiografia" in canon_exames("radiografia de perfil da face")
    assert "telerradiografia" in canon_exames("Rx perfil")
    assert "telerradiografia" in canon_exames("Raio-X de perfil")


def test_foto_de_perfil_continua_so_fotografia():
    assert canon_exames("foto de perfil") == {"fotografia"}
    assert canon_exames("fotografia perfil direito") == {"fotografia"}
