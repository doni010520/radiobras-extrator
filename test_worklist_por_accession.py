"""Exame com laudo pronto que o robo nao achava: busca so por NOME e so no DIA da guia.

Casos (frente C, 08/10): LUCIANA SILVA DE BARROS 197100679 (pedido 40349639, guia de
08/09, exame lancado em 10/09, laudo "Impresso", pendencia ABERTA), PATRICIA
197467257 (40352362), MATEUS 197109522, ROSANGELA 197455184, DENISE 197519247. A
busca por nome do PRORADIS e por PREFIXO e quebra com espaco duplo/grafia da linha;
e a janela de +-7 dias so existe no caminho sem analitico. A mensagem mandava
"cobrar o laudo do radiologista" de um laudo que ja existia.

Busca pelo NUMERO DO PEDIDO (accession, que veio do analitico filtrado pelo
convenio), numa janela de dias, aceitando so a linha com o accession EXATO.
"""
import extrator_arquivos as ea


def _linha(acc, nome="LUCIANA SILVA DE BARROS"):
    return (f'<tr id="tr_{acc}"><td><input data-accession-no="{acc}"></td>'
            f'<td><span class="wrap-name">{nome}</span></td><td>{acc}</td></tr>')


class _Page:
    def __init__(self, html_por_tipo):
        self.html = html_por_tipo
        self.chamadas = []

    def evaluate(self, js, args):
        self.chamadas.append(args)
        return self.html.get(args[3], "<table></table>")


def test_acha_pelo_numero_fora_do_dia_da_guia():
    p = _Page({"study_datetime": "<table>" + _linha("40349639") + "</table>"})
    r = ea._buscar_na_worklist_por_accession(p, "40349639", "08/09/2026")
    assert r and r["accession"] == "40349639"
    assert r["nome"] == "LUCIANA SILVA DE BARROS"
    acc, inicio, fim, tipo = p.chamadas[0]
    assert acc == "40349639"
    assert inicio.startswith("24/08/2026") and fim.startswith("23/09/2026")  # +-15 dias


def test_so_aceita_o_accession_exato():
    p = _Page({"study_datetime": "<table>" + _linha("40349640", "OUTRA PESSOA") + "</table>",
               "realized": "<table>" + _linha("40349640", "OUTRA PESSOA") + "</table>"})
    assert ea._buscar_na_worklist_por_accession(p, "40349639", "08/09/2026") is None


def test_tenta_a_data_de_realizacao_se_a_do_estudo_nao_achar():
    p = _Page({"study_datetime": "<table></table>",
               "realized": "<table>" + _linha("40349639") + "</table>"})
    assert ea._buscar_na_worklist_por_accession(p, "40349639", "08/09/2026")


def test_accession_invalido_nem_consulta():
    p = _Page({})
    assert ea._buscar_na_worklist_por_accession(p, "WL123", "08/09/2026") is None
    assert ea._buscar_na_worklist_por_accession(p, "", "08/09/2026") is None
    assert p.chamadas == []


def test_erro_de_consulta_nao_levanta():
    class _P:
        def evaluate(self, *a):
            raise RuntimeError("timeout")
    assert ea._buscar_na_worklist_por_accession(_P(), "40349639", "08/09/2026") is None
