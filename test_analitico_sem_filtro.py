"""O relatorio analitico NUNCA e pedido sem o filtro de convenio.

Execs 1108 (11/09), 1218, 1495 (25/09) e 1585 (26/09): a tela de relatorios nao
carregou a lista de convenios, `resolve_tokens` devolveu [] e o relatorio foi pedido
com insurance="" — TODOS os convenios (227, 228, 241 e 126 pacientes no lugar de 12
a 24). O filtro que separa exame particular da guia RedeUna parte do principio de
que o analitico ja vem filtrado; sem isso ele nao protege. Agora: lista incompleta
-> recarrega; continua incompleta -> falha tecnica, a rodada aborta e nada e pedido.
"""
import pytest

import extrator_arquivos as ea


class _Opt:
    def __init__(self, txt, val):
        self.txt, self.val = txt, val

    def inner_text(self):
        return self.txt

    def get_attribute(self, k):
        return self.val


class _Page:
    """Pagina cujos <option> de convenio so aparecem a partir da carga `ok_na`."""

    def __init__(self, ok_na):
        self.cargas = 0
        self.ok_na = ok_na

    def goto(self, *a, **k):
        self.cargas += 1

    def evaluate(self, *a, **k):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def query_selector_all(self, sel):
        if "insurance" in sel:
            if self.cargas >= self.ok_na:
                return [_Opt("REDE UNNA - CENTRO", "c1"), _Opt("REDE UNNA - LAURO", "c2")]
            return []
        return [_Opt("CENTRO", "s1")]

    class context:
        @staticmethod
        def cookies():
            return []


@pytest.fixture
def sem_rede(monkeypatch):
    pedidos = []
    monkeypatch.setattr(ea.time, "sleep", lambda s: None)
    monkeypatch.setattr(ea, "post_relatorio",
                        lambda cookies, ins, seg, de, ate: pedidos.append(ins) or "<html/>")
    monkeypatch.setattr(ea, "parse_html_to_df", lambda html: ("DF", None, None))
    return pedidos


def test_lista_de_convenios_que_nunca_carrega_aborta_sem_pedir(sem_rede):
    with pytest.raises(RuntimeError, match="falha técnica"):
        ea._get_relatorio_analitico(_Page(ok_na=99), ["REDE UNNA - CENTRO"], [], "17/09/2026")
    assert sem_rede == [], "nunca pedir o relatorio sem o filtro de convenio"


def test_lista_que_carrega_na_segunda_vez_segue_filtrada(sem_rede):
    df = ea._get_relatorio_analitico(_Page(ok_na=2), ["REDE UNNA - CENTRO"], [], "17/09/2026")
    assert df == "DF"
    assert sem_rede == [["c1"]]


def test_convenio_configurado_que_nao_existe_tambem_aborta(sem_rede):
    """Um dos convenios do plano sumiu da lista: pedir so com os outros faria
    guia da unidade faltante cair como 'nao encontrado'. Melhor parar e avisar."""
    with pytest.raises(RuntimeError, match="falha técnica"):
        ea._get_relatorio_analitico(_Page(ok_na=1),
                                    ["REDE UNNA - CENTRO", "REDE UNNA - PERIPERI"], [],
                                    "17/09/2026")
    assert sem_rede == []
