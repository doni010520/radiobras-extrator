"""Nenhum módulo do robô pode usar um nome que não existe.

Caso 15-18/10: um refactor apagou `_faturar_cron_running = threading.Event()` do
app.py. O agendador engolia o NameError a cada 30 min e o faturamento automático
ficou 3 dias parado sem nenhum aviso. Todos os testes passavam."""
import glob
import os

import pytest

pyflakes_api = pytest.importorskip("pyflakes.api")
from pyflakes import messages  # noqa: E402
from pyflakes.reporter import Reporter  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))


class _Coleta(Reporter):
    def __init__(self):
        self.erros = []

    def unexpectedError(self, filename, msg):
        self.erros.append(f"{filename}: {msg}")

    def syntaxError(self, filename, msg, lineno, offset, text):
        self.erros.append(f"{filename}:{lineno}: {msg}")

    def flake(self, message):
        if isinstance(message, messages.UndefinedName):
            self.erros.append(str(message))


def test_nenhum_nome_indefinido():
    rep = _Coleta()
    for f in sorted(glob.glob(os.path.join(AQUI, "*.py"))):
        if os.path.basename(f).startswith("test_"):
            continue
        with open(f, encoding="utf-8") as fh:
            pyflakes_api.check(fh.read(), f, rep)
    assert not rep.erros, "\n".join(rep.erros)
