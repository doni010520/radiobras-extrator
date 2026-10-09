"""Toca todos os cassetes gravados e compara com a linha de base. Sem a pasta
(servidor, CI), o teste e pulado: cassete tem documento medico e nao vai pro git."""
import json
import os

import pytest

import replay_comparar as rc
import replay_harness as rh

BASE = os.path.join(rh.PASTA_PADRAO, "baseline.json")


@pytest.mark.skipif(not os.path.exists(BASE), reason="sem cassetes nesta maquina")
def test_decisoes_iguais_a_linha_de_base():
    base = json.load(open(BASE, encoding="utf-8"))
    dif = rc.diferencas(base, rc.tocar_todos())
    assert not dif, "\n".join(dif[:40])


def test_diferencas_aponta_guia_que_mudou():
    base = {"c1": {"1": {"categoria": "auto"}}}
    atual = {"c1": {"1": {"categoria": "sem_exame"}}}
    assert rc.diferencas(base, atual) == ["c1 gto 1: categoria auto -> sem_exame"]
