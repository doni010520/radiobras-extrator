"""MODULO ENTREGA: onde vive o exame de MODELO.

Descoberto em 22/08, depois de o robo passar semanas sem achar o entregavel do
modelo. O exame de MODELO (procedimento 81000308) **nao existe na worklist de
laudos** (`reports_list`) — que e o unico lugar onde a esteira procurava. Provas:
a worklist de 13/08 tem 93 accessions, de 40342100 a 40342210, e o do modelo do
HANIEL e 40342410; busca por nome numa janela de 22 dias nao devolve nada.

Ele vive no modulo **Entrega** (`/delivery/get_list`), e com uma pegadinha: o
filtro `origens` vem 'EXTERNO' por padrao na tela, e com esse valor o modelo NAO
aparece. So com `origens` VAZIO.

O entregavel sai de `delivery/print_series` com o study_id: uma folha A4 com a logo
RadioBras, o cabecalho do paciente e o render 3D em 5 vistas (frontal em oclusao,
duas laterais, oclusal superior e inferior). E a folha A4 que se anexa — ela
identifica o paciente, que a imagem crua de 1920x1080 nao faz (decisao do dono,
22/08). O filtro da logo verde ja seleciona ela sozinho; nao se mexe nessa trava."""
from entrega import parse_linhas, eh_modelo, montar_body


# Fragmento com a MESMA estrutura da resposta real (14 <td>, checkbox com
# data-accession-no e value=study_id).
_HTML = """
<table><tbody>
<tr>
  <td class="td-tip no-print"><select><option data-alias="ATENÇÃO" data-name="SEM LAUDO"></option></select></td>
  <td class="delivery-actions">
    <input type="checkbox" name="deliver_check" class="deliver-check"
           data-accession-no="40342410" value="STUDYHASH_MODELO">
    <a href="#" title="Imprimir exame" onclick="post_to_new_window(base_url('delivery/print_series'), 'print-series', {study_id : 'STUDYHASH_MODELO'}); event.stopPropagation()">i</a>
  </td>
  <td>A Laudar</td><td> 40342410 </td><td> 13/08/2026 16:09 </td>
  <td> RADIOBRAS </td><td> MODELO </td><td> HANIEL OLIVEIRA ALMEIDA </td>
  <td>leticia costa</td><td>, </td><td> BUSCAR </td><td> 18/08/2019 </td>
  <td></td><td> LAURO </td>
</tr>
<tr>
  <td class="td-tip no-print"></td>
  <td class="delivery-actions">
    <input type="checkbox" name="deliver_check" class="deliver-check"
           data-accession-no="40343905" value="STUDYHASH_PAN">
  </td>
  <td>Laudado</td><td> 40343905 </td><td> 19/08/2026 08:25 </td>
  <td> RADIOBRAS </td><td> PANORAMICA </td><td> RICARDO BISPO DO ROSARIO SILVA </td>
  <td>REBECA</td><td>, </td><td> ENTREGUE </td><td> 26/06/1984 </td>
  <td></td><td> CENTRO </td>
</tr>
</tbody></table>
"""


# ── parse da lista ──────────────────────────────────────────────────────────
def test_le_as_duas_linhas():
    assert len(parse_linhas(_HTML)) == 2


def test_campos_da_linha_do_modelo():
    li = [x for x in parse_linhas(_HTML) if x["accession"] == "40342410"][0]
    assert li["exame"] == "MODELO"
    assert li["paciente"] == "HANIEL OLIVEIRA ALMEIDA"
    assert li["dia"] == "13/08/2026"
    assert li["unidade"] == "LAURO"


def test_study_id_vem_do_checkbox():
    """O study_id do checkbox e o MESMO que o print_series usa — pegar dali e mais
    robusto do que parsear o onclick, que pode mudar de formato."""
    li = [x for x in parse_linhas(_HTML) if x["accession"] == "40342410"][0]
    assert li["study_id"] == "STUDYHASH_MODELO"


def test_linha_sem_checkbox_e_ignorada():
    # cabecalho e linhas de layout nao tem study_id -> nao viram item
    html = "<table><tr><td>Status</td><td>Pedido</td><td>Exame</td></tr></table>"
    assert parse_linhas(html) == []


def test_html_vazio():
    assert parse_linhas("") == []
    assert parse_linhas(None) == []


# ── reconhecer o exame de modelo ────────────────────────────────────────────
def test_modelo_e_modelo():
    for e in ("MODELO", "modelo", " Modelo ", "MODELO [81000308]"):
        assert eh_modelo(e) is True, e


def test_documentacao_com_modelo_NAO_e_guia_de_modelo():
    """Pegadinha: 'DOCUMENTACAO COMPLETA C/MODELO' e um PACOTE (imagens + laudos +
    o modelo) e TEM laudo. Confundir os dois faria o robo parar de exigir laudo de
    157 guias de documentacao — o oposto do que se quer."""
    for e in ("DOCUMENTAÇÃO COMPLETA C/MODELO",
              "DOCUMENTAÇÃO COMPLETA COM MODELO DIGITAL",
              "DOCUMENTACAO COMPLETA C/MODELO [Grupo]"):
        assert eh_modelo(e) is False, e


def test_outros_exames_nao_sao_modelo():
    for e in ("PANORAMICA", "TELERRADIOGRAFIA LATERAL", "PERIAPICAL", "", None):
        assert eh_modelo(e) is False, e


# ── o corpo da consulta ─────────────────────────────────────────────────────
def test_origens_vai_VAZIO():
    """A pegadinha que escondeu o modelo por semanas: a tela manda
    origens='EXTERNO' e com esse valor o modelo nao aparece na lista."""
    body = montar_body("01/08/2026", "22/08/2026")
    assert "filtro%5Borigens%5D=&" in body + "&" or "filtro[origens]=&" in body + "&"
    assert "EXTERNO" not in body


def test_body_leva_o_periodo_e_todos_os_exames():
    body = montar_body("01/08/2026", "22/08/2026")
    assert "01%2F08%2F2026" in body or "01/08/2026" in body
    assert "todos" in body


def test_body_aceita_filtro_por_nome():
    body = montar_body("01/08/2026", "22/08/2026", nome="HANIEL")
    assert "HANIEL" in body


# ── qual imagem anexar (decisao do dono, 22/08) ─────────────────────────────
# Medido nos 3 casos reais de agosto: a folha A4 com logo+cabecalho existe em 1 de
# 3. HANIEL (13/08) tem; RICARDO (19/08) so tem a grade crua 708x818 com as mesmas
# 6 vistas; LUIZA (20/08) nao tem nada ainda. Travar na A4 deixaria o RICARDO
# parado esperando alguem gerar a folha — o trabalho humano que se quer eliminar.
#
# Regra: A4 quando existe; senao, TODAS as cruas. Nunca uma vista sozinha — isso
# entregaria menos angulos do que o exame tem.
from entrega import escolher_entregaveis


def _i(nome, logo):
    return {"bytes": nome.encode(), "logo": logo}


def test_com_A4_anexa_SO_a_A4():
    """HANIEL: 1 folha com logo + 6 vistas cruas. Anexar as 7 encheria a guia de
    imagem repetida — a A4 ja traz as mesmas vistas."""
    itens = [_i("a4", True)] + [_i("crua%d" % n, False) for n in range(6)]
    esc = escolher_entregaveis(itens)
    assert len(esc) == 1
    assert esc[0]["bytes"] == b"a4"


def test_sem_A4_anexa_as_cruas():
    # RICARDO: so a grade. Sem esta regra a guia dele nao faturaria.
    itens = [_i("grade", False)]
    esc = escolher_entregaveis(itens)
    assert [x["bytes"] for x in esc] == [b"grade"]


def test_sem_A4_e_com_varias_cruas_anexa_TODAS():
    """Nunca escolher 'a maior' — entregaria uma vista so. Todas ou a A4."""
    itens = [_i("v%d" % n, False) for n in range(6)]
    assert len(escolher_entregaveis(itens)) == 6


def test_sem_imagem_nenhuma():
    # LUIZA: o render ainda nao foi gerado -> nao ha o que anexar (pendencia real)
    assert escolher_entregaveis([]) == []
    assert escolher_entregaveis(None) == []


def test_varias_A4_todas_entram():
    itens = [_i("a4a", True), _i("a4b", True), _i("crua", False)]
    esc = escolher_entregaveis(itens)
    assert sorted(x["bytes"] for x in esc) == [b"a4a", b"a4b"]


# ── a pendencia do modelo sem render ────────────────────────────────────────
_MSG_SEM_RENDER = ("o render 3D do MODELO ainda nao foi gerado no PRORADIS — nao ha "
                   "entregavel para anexar. O robo anexa sozinho assim que o render "
                   "sair; cobrar a geracao do modelo.")


def test_modelo_sem_render_nao_manda_cobrar_LAUDO():
    """Caso LUIZA (20/08): print_series devolveu ZERO imagem. Antes disso a guia
    caia em 'sem_entregavel', cuja acao manda COBRAR A EMISSAO DO LAUDO — de um
    exame que por definicao nao tem laudo. Era o estrago original."""
    from db import classificar_pendencia
    chave, quem, acao = classificar_pendencia(_MSG_SEM_RENDER, "sem_exame")
    assert chave == "modelo_sem_render"
    assert quem == "Radiologista"
    assert "render" in acao.lower()
    assert "emissão do laudo" not in acao


def test_modelo_sem_render_NAO_e_falha_nossa():
    """Nao adianta reprocessar: o render nao existe. Marcar como nossa poria a guia
    num loop de retry que nunca resolve."""
    from db import eh_nosso, eh_pendencia_front
    assert eh_nosso(_MSG_SEM_RENDER, "sem_exame") is False
    assert eh_pendencia_front(_MSG_SEM_RENDER, "sem_exame") is True


def test_sem_entregavel_comum_nao_foi_atropelado():
    from db import classificar_pendencia
    m = "o exame existe no PRORADIS, mas não há laudo nem imagem para baixar"
    assert classificar_pendencia(m, "sem_exame")[0] == "sem_entregavel"
