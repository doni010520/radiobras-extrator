"""O PACIENTE da linha da worklist e o 1o `.wrap-name` — nao "o maior texto".

**Bug medido em 23/08 (300 linhas reais de 3 dias): 59 delas — 19,7% — sairam com o
nome ERRADO.** O parser escolhia o paciente por heuristica: entre as celulas, o
MAIOR texto em maiusculas de 10 a 55 caracteres. So que a linha tem tres celulas
`.wrap-name` — paciente, solicitante e uma vazia — e o nome do DENTISTA costuma ser
mais longo que o do paciente:

    td[07] .wrap-exam   PANORAMICA
    td[08] .wrap-name   HOSANA BARRETO DOS SANTOS       (25 chars)  <- a paciente
    td[12] .wrap-name   ENEIAS PEREIRA DA SILVA NETO    (28 chars)  <- o dentista

Consequencia real: a guia 196346585 da HOSANA (18/08, R$ 49,79) morreu como
"paciente nao foi encontrado no PRORADIS" — enquanto o exame estava la, com laudo,
accession 40343815. A esteira procurava "HOSANA" numa lista que dizia "ENEIAS".

Outros casos medidos no mesmo dia: DILMA DA CONCEICAO virava SUELY NEVES DE JESUS
GONCALVES; ANAILDES SANTOS virava SUS SEM IDENTIFICACAO; JESSICA SANTOS DE JESUS
virava LUCIO PAULO SOARES DE CARVALHO JUNIOR.

A ancora `.wrap-name` estava em 100% das 300 linhas — a heuristica fica so como
rede de seguranca, para o dia em que o SmartRIS mudar o HTML."""
from extrator_arquivos import _parse_worklist_html


# Estrutura REAL, colhida da linha da HOSANA em 18/08 (accession 40343815).
_LINHA = """
<table><tr id="tr_x">
  <td class="td-tip no-printable"><span class="workflow-chip prd-chip">(ATENÇÃO) SEM LAUDO</span></td>
  <td class="no-printable"><div class="wrap-controls"></div></td>
  <td>2026-08-18</td>
  <td><span class="tag tag_P">Impresso</span></td>
  <td class="wrap-accession">40343815</td>
  <td>202608181537 18/08/2026 15:37</td>
  <td>RADIOBRAS</td>
  <td><span class="wrap-exam">PANORAMICA</span></td>
  <td><span class="wrap-name">HOSANA BARRETO DOS SANTOS</span></td>
  <td><span class="wrap-verified">germanapir...</span></td>
  <td>BUSCAR</td>
  <td>PERIPERI</td>
  <td><span class="wrap-name">ENEIAS PEREIRA DA SILVA NETO</span></td>
  <td><span class="wrap-name"></span></td>
</tr></table>
"""


def test_pega_o_paciente_e_nao_o_dentista():
    """O caso HOSANA: R$ 49,79 parados porque o dentista tem nome mais longo."""
    by = {}
    _parse_worklist_html(_LINHA, by)
    assert by["40343815"]["nome"] == "HOSANA BARRETO DOS SANTOS"


def test_dilma_nao_vira_suely():
    """Medido em 18/08: DILMA DA CONCEICAO (18 chars) perdia para o solicitante
    SUELY NEVES DE JESUS GONCALVES (30 chars)."""
    h = (_LINHA.replace("HOSANA BARRETO DOS SANTOS", "DILMA DA CONCEICAO")
               .replace("ENEIAS PEREIRA DA SILVA NETO", "SUELY NEVES DE JESUS GONCALVES"))
    by = {}
    _parse_worklist_html(h, by)
    assert by["40343815"]["nome"] == "DILMA DA CONCEICAO"


def test_nome_curto_de_paciente_sobrevive():
    """ANAILDES SANTOS perdia para 'SUS SEM IDENTIFICACAO' de outra celula."""
    h = _LINHA.replace("HOSANA BARRETO DOS SANTOS", "ANAILDES SANTOS")
    h = h.replace("<td>BUSCAR</td>", "<td>SUS SEM IDENTIFICACAO</td>")
    by = {}
    _parse_worklist_html(h, by)
    assert by["40343815"]["nome"] == "ANAILDES SANTOS"


def test_paciente_com_nome_mais_longo_continua_certo():
    """O caminho que ja funcionava nao pode regredir."""
    h = _LINHA.replace("ENEIAS PEREIRA DA SILVA NETO", "ANA LIMA")
    by = {}
    _parse_worklist_html(h, by)
    assert by["40343815"]["nome"] == "HOSANA BARRETO DOS SANTOS"


def test_sem_wrap_name_cai_na_heuristica_antiga():
    """Rede de seguranca para o dia em que o SmartRIS mudar o HTML: sem a ancora,
    volta a valer o comportamento anterior — que erra as vezes, mas nao quebra."""
    h = _LINHA.replace('class="wrap-name"', 'class="outra-coisa"')
    by = {}
    _parse_worklist_html(h, by)
    assert by["40343815"]["nome"] == "ENEIAS PEREIRA DA SILVA NETO"


def test_wrap_name_vazio_nao_engole_a_linha():
    """A 3a celula .wrap-name vem VAZIA na estrutura real. Se o parser pegasse a
    primeira sem checar conteudo, o nome viria em branco e TODA guia daria
    'paciente nao encontrado' — de bug em 20% para bug em 100%."""
    h = _LINHA.replace('<span class="wrap-name">HOSANA BARRETO DOS SANTOS</span>',
                       '<span class="wrap-name">   </span>')
    by = {}
    _parse_worklist_html(h, by)
    assert by["40343815"]["nome"] == "ENEIAS PEREIRA DA SILVA NETO"


def test_linha_sem_accession_e_ignorada():
    by = {}
    _parse_worklist_html("<table><tr><td>cabecalho</td></tr></table>", by)
    assert by == {}


def test_acumula_varias_linhas():
    h2 = (_LINHA.replace("40343815", "40343817")
                .replace("HOSANA BARRETO DOS SANTOS", "OUTRO PACIENTE AQUI"))
    by = {}
    _parse_worklist_html(_LINHA, by)
    _parse_worklist_html(h2, by)
    assert by["40343815"]["nome"] == "HOSANA BARRETO DOS SANTOS"
    assert by["40343817"]["nome"] == "OUTRO PACIENTE AQUI"
