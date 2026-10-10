"""goto atropelado por redirecionamento em curso (replay 05/10, 10/10).

Logo apos o login o PRORADIS ainda redireciona; o goto para admin_reports morria com
"interrupted by another navigation" e a rodada inteira abortava."""
import pytest

import extrator_arquivos as ea

_INTERROMPIDO = ('Page.goto: Navigation to "https://x/ris/admin_reports" is interrupted '
                 'by another navigation to "https://x/ris/home"')


class _Pg:
    def __init__(self, erros):
        self.erros = list(erros)
        self.gotos = 0

    def goto(self, url, **kw):
        self.gotos += 1
        if self.erros:
            raise RuntimeError(self.erros.pop(0))
        return "ok"

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        pass


def test_redirecionamento_em_curso_tenta_de_novo():
    pg = _Pg([_INTERROMPIDO])
    assert ea.ir_para(pg, "u", wait_until="networkidle") == "ok"
    assert pg.gotos == 2


def test_outro_erro_sobe_na_hora():
    pg = _Pg(["net::ERR_CONNECTION_RESET"])
    with pytest.raises(RuntimeError, match="RESET"):
        ea.ir_para(pg, "u")
    assert pg.gotos == 1


def test_desiste_depois_de_3():
    pg = _Pg([_INTERROMPIDO] * 5)
    with pytest.raises(RuntimeError, match="interrupted"):
        ea.ir_para(pg, "u")
    assert pg.gotos == 3
