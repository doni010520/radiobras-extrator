"""Toca todos os cassetes gravados e compara com a linha de base. Sem a pasta
(servidor, CI), o teste e pulado: cassete tem documento medico e nao vai pro git.

Roda em PROCESSO SEPARADO (10/10): no processo do pytest o app ja foi importado
por outros testes e sobe as threads agendadas dele; junto com os leitores da
esteira, o PyMuPDF (nao e thread-safe) derrubou a suite com 'access violation'."""
import os
import subprocess
import sys

import pytest

import replay_comparar as rc
import replay_harness as rh

BASE = os.path.join(rh.PASTA_PADRAO, "baseline.json")


@pytest.mark.skipif(not os.path.exists(BASE), reason="sem cassetes nesta maquina")
def test_decisoes_iguais_a_linha_de_base():
    amb = {k: v for k, v in os.environ.items() if k not in ("GEMINI_API_KEY", "ODONTO_PROXY_URL")}
    amb["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "replay_comparar.py"], cwd=os.path.dirname(__file__) or ".",
                       capture_output=True, text=True, encoding="utf-8", env=amb, timeout=900)
    saida = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0 and "IGUAL" in saida, saida[-3000:]


def test_diferencas_aponta_guia_que_mudou():
    base = {"c1": {"1": {"categoria": "auto"}}}
    atual = {"c1": {"1": {"categoria": "sem_exame"}}}
    assert rc.diferencas(base, atual) == ["c1 gto 1: categoria auto -> sem_exame"]
