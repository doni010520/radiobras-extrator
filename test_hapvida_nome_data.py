"""Histórico Hapvida set/26 (Centro): falsas pendências que a clínica anexou e a Hapvida pagou."""
import datetime as dt

import pytest

import hapvida_decisao as hd
import hapvida_pedido as hp


@pytest.mark.parametrize("lido,guia", [
    ("Jorge Pimentel", "JORGE PIMENTEL DOS SANTOS"),
    ("Mara de Jesus Lopez de Sena", "MARIA DE JESUS LOPES DE SOUSA"),
    ("Natuele Souza", "NATIELE SOUZA DA SILVA"),
    ("Julio Andre", "JULIO ANDRE SILVA BOTELHO"),
])
def test_nome_parcial_aceita(lido, guia):
    assert hp.nome_parcial(lido, guia)


@pytest.mark.parametrize("lido,guia", [
    ("Matheus Stone Vasconcelos", "DEBORA GONCALVES STOWE"),   # outra pessoa
    ("Gouldina", "GALDINO MESSIAS SANTANA DA SILVA"),           # só um nome
    ("Liana D. S. Lu", "LIANA DANTAS SILVA"),                   # sobrenome só em inicial
    ("Erlon Domingo dos Santos", "ERALDO DOMINGOS DOS SANTOS"), # 1º nome diferente
    ("Antelmy Sara Damasceno", "ANTHONY JORGE DAMACENA OLIVEIRA"),
])
def test_nome_parcial_recusa(lido, guia):
    assert not hp.nome_parcial(lido, guia)


def _guia(nome):
    return {"nome": nome, "dia": "03/09/2026", "itens": [{"codigo": "81000405"}]}


def test_nome_vazio_no_portal_usa_nome_da_lista():
    pr = {"encontrado": True, "nome": "MAGNOLIA GOMES DE CARVALHO", "nascimento": "",
          "entregaveis": ["x.jpg"], "pedido": ["p.jpg"]}
    d = hd.decidir_guia(_guia("MAGNOLIA GOMES"), False, {"nome": "", "nascimento": ""}, pr)
    assert d.categoria != hd.IDENTIDADE


def test_nome_vazio_nao_libera_outra_pessoa():
    pr = {"encontrado": True, "nome": "FLAVIA SANTOS", "nascimento": "",
          "entregaveis": ["x.jpg"], "pedido": ["p.jpg"]}
    d = hd.decidir_guia(_guia("FLAVIA QUEIROZ"), False, {"nome": "", "nascimento": ""}, pr)
    assert d.categoria == hd.IDENTIDADE


class _Resp:
    def __init__(self, txt):
        self.text, self.usage_metadata = txt, None


class _Gem:
    def __init__(self, txt):
        self.models = self
        self._t = txt

    def generate_content(self, **k):
        return _Resp(self._t)


def test_reler_data_corrige_ano():
    assert hp._reler_data(_Gem('{"data": "08/09/2026"}'), ("p.jpg", "image/jpeg", b"x", None)) == dt.date(2026, 9, 8)


def test_reler_data_falha_devolve_none():
    assert hp._reler_data(_Gem("lixo"), ("p.jpg", "image/jpeg", b"x", None)) is None
