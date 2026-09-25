"""Parsers das telas do portal Hapvida. HTML SINTÉTICO (o repositório é público)."""
import hapvida_portal as hp

EXECUTADOS = """
<table><tr><th>PROCESSO</th><th>ESPECIALIDADE</th><th>COD.USUÁRIO</th><th>NOME USUÁRIO</th><th>GUIA</th>
<th>CODIGO</th><th>NOME PROCEDIMENTO</th><th>DENTE</th><th>DT ATENDIMENTO</th><th>STATUS</th><th>VALOR</th><th>VALOR FRANQUIA</th></tr>
<tr><td></td><td>EXAMES COMPLEME</td><td>X0000000000001</td><td>ANA TESTE</td><td>900001</td><td>81000405</td>
<td>RADIOGRAFIA PANORAMI</td><td>18/48</td><td>01/09/2026 08:54</td><td>EM PROCESSAMENTO</td><td>29,17</td><td>0,00</td></tr>
<tr><td></td><td>RADIOLOGIA</td><td>X0000000000002</td><td>BRUNO TESTE</td><td>900002</td><td>81000421</td>
<td>RADIOGRAFIA PERIAPIC</td><td>24/25</td><td>01/09/2026 16:49</td><td>EM PROCESSAMENTO</td><td>5,57</td><td>0,00</td></tr>
<tr><td colspan="12">Total</td></tr></table>
"""

PACIENTE = """<div>Usuário Protocolo Azul</div><b>Carteira:</b><br> X0000000000001 <br>
<b>Paciente:</b><br> ANA TESTE DA SILVA <br><b>Data Nascimento:</b><br> 21/10/1969 <br>
<b>Idade:</b><br> 56 anos<br><span>ATENDIMENTO AUTORIZADO.</span>"""

ANEXOS = """<table>
<tr class="claro" style="cursor:pointer;" data-img-id="111" data-nu-guia="900001" data-img-name="X0000000000001013-10.jpg" id-lobstore="10">
<td class="chama-img">111</td></tr>
<tr class="escuro" style="cursor:pointer;" data-img-id="112" data-nu-guia="900001" data-img-name="X0000000000001013-11.png" id-lobstore="11">
<td class="chama-img">112</td></tr></table>"""

FORM = """
<form name="form_img" method="post" enctype="multipart/form-data" action="https://exemplo/insert_img_audi_odon.php">
<input type="file" name="dados" id="dados" required>
<input type="hidden" name="diretorio" id="diretorio" value="/auditoria_odonto/imagens">
<input type="hidden" value="2" name="pAmbOrg" id="pAmbOrg">
</form>
<form name="form_proced" method="post" action="WebDentalAtendimento.pr_status_upload">
<input type="hidden" name="pguia" value="900002" id="pguia">
<input type="hidden" name="id_lobstore" value="99" >
<input type="checkbox" id="item_1" value="1" name="pnu_item" >
<input type="checkbox" id="item_2" value="2" name="pnu_item" >
</form>"""


def test_parse_executados_ignora_cabecalho_e_total():
    ls = hp.parse_executados(EXECUTADOS)
    assert [l["guia"] for l in ls] == ["900001", "900002"]
    assert ls[0]["codigo"] == "81000405" and ls[0]["usuario"] == "X0000000000001"
    assert ls[1]["dt_atendimento"] == "01/09/2026 16:49"


def test_parse_paciente_traz_nascimento():
    p = hp.parse_paciente(PACIENTE)
    assert p == {"carteira": "X0000000000001", "nome": "ANA TESTE DA SILVA",
                 "nascimento": "21/10/1969", "autorizado": True}


def test_parse_anexos_por_guia():
    a = hp.parse_anexos(ANEXOS)
    assert {x["guia"] for x in a} == {"900001"} and len(a) == 2


def test_sem_imagem_da_lista_vazia():
    assert hp.parse_anexos("<td>Sem imagem nesse período.</td>") == []


def test_parse_form_upload():
    f = hp.parse_form_upload(FORM)
    assert f["acao_img"].endswith("insert_img_audi_odon.php")
    assert f["campos_img"]["diretorio"] == "/auditoria_odonto/imagens"
    assert "dados" not in f["campos_img"]
    assert f["acao_proced"] == "WebDentalAtendimento.pr_status_upload"
    assert f["campos_proced"]["pguia"] == "900002" and f["itens"] == [1, 2]


def test_guia_tem_imagem_usa_banco_de_imagens():
    class S:
        cd_pessoa = "1"
        def get(self, proc, **kw):
            return ANEXOS
    portal = hp.PortalHapvida("centro", sessao=S())
    assert portal.guia_tem_imagem("X0000000000001", "900001", "01/09/2026") is True
    assert portal.guia_tem_imagem("X0000000000001", "900002", "01/09/2026") is False
