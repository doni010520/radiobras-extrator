"""O prazo da OdontoPrev e de 7 DIAS a partir do exame — para tudo (imagem e
laudo). Passou disso, so por RECURSO DE GLOSA; anexar deixa de ser possivel.

Regra informada pelo dono em 28/08, depois da auditoria de cobertura: das 93 guias
com documentacao incompleta, 80 ja estavam fora da janela. Seis delas (exame de
20/08) perderam por UM dia.

O sistema estava calibrado para outro numero: `FATURAR_PRAZO_DIAS=17` em producao.
Consequencias medidas:
  - o alerta de SLA dispara quando faltam 2 dias para os 17, ou seja no DIA 15 —
    oito dias depois de a guia ter morrido;
  - o cron reprocessa dias 8 a 17, que nao podem mais virar faturamento;
  - o dia-alvo D-4 deixa so 3 dias de margem.
"""
import os
from datetime import date, timedelta

import app
import db


def test_sla_conta_os_7_dias_da_operadora_e_nao_a_janela_do_cron():
    """A janela do cron (FATURAR_PRAZO_DIAS) diz ate onde REPROCESSAR; o prazo da
    operadora diz quando a guia MORRE. Sao coisas diferentes e o SLA tem que seguir
    a segunda — senao avisa depois do enterro."""
    # PRODUCAO roda com FATURAR_PRAZO_DIAS=17. Sem fixar aqui, o teste passaria
    # por acidente no ambiente local (que cai no default 7) e nao protegeria nada.
    antes = os.environ.get("FATURAR_PRAZO_DIAS")
    os.environ["FATURAR_PRAZO_DIAS"] = "17"
    try:
        exame = (date.today() - timedelta(days=5)).strftime("%d/%m/%Y")
        assert app._sla_dias_restantes(exame) == 2
    finally:
        if antes is None:
            os.environ.pop("FATURAR_PRAZO_DIAS", None)
        else:
            os.environ["FATURAR_PRAZO_DIAS"] = antes


def test_guia_com_mais_de_7_dias_virou_recurso():
    assert db.virou_recurso("20/08/2026", hoje=date(2026, 8, 28)) is True


def test_guia_no_setimo_dia_ainda_da_para_anexar():
    assert db.virou_recurso("21/08/2026", hoje=date(2026, 8, 28)) is False


def test_dia_alvo_do_cron_respeita_o_tempo_do_laudo():
    """D-4 fica. Tentei D-1 em 28/08 para ganhar margem e o dono corrigiu: o laudo
    leva 3-4 dias para sair, entao D-1 acharia quase tudo sem laudo e produziria
    pendencia falsa em massa. A margem curta se resolve no ALERTA (7 dias), nao
    antecipando a rodada."""
    assert app._dia_alvo_cron(date(2026, 8, 10)) == "06/08/2026"
