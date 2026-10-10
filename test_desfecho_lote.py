"""Demonstrativo em lote (10/10).

Uma consulta por guia (8-10s) fazia a atualizacao diaria do desfecho levar horas:
nada gravado desde 25/09 e o job pendurado travava o deploy. E glosa integral
(liberado R$ 0,00) era lida como "sem dados" e aparecia como AGUARDANDO."""
import desfecho as _d
import desfecho_extrator as de

TELA = """Dados Gerais
Número total de guias:
3
Valor total bruto:
R$ 63,18
Valor total glosado:
R$ 102,22
Pagamento
Data de Pagamento\tValor Bruto\tValor IR\tValor INSS (PF)\tValor Líquido\tBanco\tAgência\tConta
{pag}
Os valores descritos estão separados por lotes de pagamentos.
Clique na seta para expandir a relação de GTOs
Dentista
Código: 397950
Guia: 194462882
Nome: GIOVANNA ALMEIDA SALDANHA
Carteirinha: 645828940
Valor Liberado: R$ 31,59
Valor Glosado: R$ 0,00
Pagamento autorizado
Guia: 194474509
Nome: ELIANE DE JESUS LEAL
Carteirinha: 647408480
Valor Liberado: R$ 0,00
Valor Glosado: R$ 102,22
Pagamento glosado
Guia: 194500459
Nome: MARCOS VINICIUS AMORIM FEITOSA
Carteirinha: 457021590
Valor Liberado: R$ 1.031,59
Valor Glosado: R$ 0,00
Pagamento autorizado
Voltar ao topo"""


def _tela(pag="Não há dados disponíveis"):
    return TELA.format(pag=pag)


def test_le_cada_guia_do_lote():
    r = de.ler_demo_lote(_tela())
    assert set(r["guias"]) == {"194462882", "194474509", "194500459"}
    assert r["guias"]["194500459"]["liberado"] == 1031.59
    assert r["datas_pagamento"] == []


def test_glosa_integral_vira_glosada_e_nao_aguardando():
    d = de.ler_demo_lote(_tela())["guias"]["194474509"]
    assert d["bruto"] == 102.22 and d["glosado"] == 102.22
    assert _d.classificar_desfecho(False, d) == "GLOSADA"


def test_autorizada_sem_pagamento_ainda_e_aguardando():
    r = de._resolver_pagamento(de.ler_demo_lote(_tela()), lambda g: None)
    assert _d.classificar_desfecho(False, r["194462882"]) == "AGUARDANDO"


def test_uma_data_de_pagamento_vale_para_as_autorizadas_do_lote():
    r = de._resolver_pagamento(de.ler_demo_lote(_tela("05/09/2026\tR$ 63,18\t...")),
                               lambda g: None)
    assert r["194462882"]["pago"] and r["194462882"]["data_repasse"] == "05/09/2026"
    assert _d.classificar_desfecho(False, r["194462882"]) == "PAGA"
    assert not r["194474509"]["pago"]          # glosada nao foi paga


def test_varias_datas_pergunta_guia_a_guia():
    perguntadas = []

    def um(g):
        perguntadas.append(g)
        return {"datas_pagamento": ["20/09/2026"] if g == "194462882" else []}
    r = de._resolver_pagamento(
        de.ler_demo_lote(_tela("05/09/2026\tR$ 1\n20/09/2026\tR$ 2")), um)
    assert sorted(perguntadas) == ["194462882", "194500459"]
    assert r["194462882"]["data_repasse"] == "20/09/2026"
    assert not r["194500459"]["pago"]


def test_guia_fora_da_resposta_fica_sem_dados():
    assert "999" not in de.ler_demo_lote(_tela())["guias"]
