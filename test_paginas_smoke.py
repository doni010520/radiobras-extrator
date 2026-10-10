"""Fumaca das paginas: protege a Fase 2 (remocao do legado, 10/10).

Toda pagina do menu abre sem erro 500, e todo link do menu aponta para uma rota
que existe. Remover rota velha nao pode quebrar tela viva nem deixar link morto."""
import re

import pytest


@pytest.fixture
def cli(monkeypatch):
    import app as app_mod
    monkeypatch.setitem(app_mod.app.before_request_funcs, None, [])
    return app_mod.app.test_client()


PAGINAS = ["/", "/faturar", "/pendencias", "/desfecho", "/relatorios/dia",
           "/relatorios/pendencias", "/glosas", "/relatorios", "/usuarios",
           "/tecnico", "/portal", "/relatorios/execucoes"]


@pytest.mark.parametrize("url", PAGINAS)
def test_pagina_abre_sem_erro(cli, url):
    r = cli.get(url)
    assert r.status_code < 500, f"{url} -> {r.status_code}"


def test_todo_link_do_menu_existe():
    import app as app_mod
    menu = open("templates/_topnav.html", encoding="utf-8").read()
    links = set(re.findall(r'href="(/[a-z0-9_/-]*)"', menu))
    rotas = {r.rule for r in app_mod.app.url_map.iter_rules()}
    mortos = sorted(l for l in links if l not in rotas)
    assert not mortos, f"links do menu sem rota: {mortos}"
