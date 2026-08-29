"""Dia SEM MOVIMENTO nao e falha — e domingo.

Medido em 27/08: o dia 23/08 (domingo) abortou nas tres unidades com "o PRORADIS
nao retornou laudos". `salvar_execucao_falha` trata todo aborto igual: enfileira o
DIA INTEIRO no retry e avisa o dono. Resultado: 6 tentativas x 3 unidades, cada uma
com login no OdontoPrev pelo proxy residencial, das 05h as 11h22 — 18 logins para
reconfirmar que domingo continua sendo domingo. E ao esgotar o teto, o dia virava
"nossa, nao recuperou" no painel.

O aviso CONTINUA nos dias uteis de proposito: se o relatorio do PRORADIS quebrar de
verdade (convenio renomeado, credencial trocada), o sintoma e exatamente esse — vir
vazio. Silenciar todo dia vazio transformaria uma quebra real em silencio."""
from datetime import date

import db

_SEM_MOVIMENTO = ("O PRORADIS não retornou laudos para 23/08/2026 nesta unidade — "
                  "os exames podem não ter sido laudados ainda, ou o dia/unidade "
                  "está incorreto. Nada a faturar.")
_LOGIN = ("Login no RedeUna/OdontoPrev falhou para o código 410923 — "
          "verifique/cadastre a senha do portal.")
_PROXY = ("Não foi possível conectar ao OdontoPrev pelo proxy (código 388336) — "
          "NÃO é a senha do portal.")


def test_dia_vazio_e_reconhecido():
    assert db.eh_dia_sem_movimento(_SEM_MOVIMENTO) is True


def test_falha_de_login_e_de_proxy_continuam_sendo_falha():
    assert db.eh_dia_sem_movimento(_LOGIN) is False
    assert db.eh_dia_sem_movimento(_PROXY) is False
    assert db.eh_dia_sem_movimento("") is False


def test_domingo_vazio_nao_avisa():
    # 23/08/2026 e domingo: vazio ali e o esperado, nao noticia.
    assert db.deve_avisar_dia_vazio("23/08/2026") is False


def test_dia_util_vazio_AVISA():
    """Guarda-corpo contra quebra silenciosa do relatorio do PRORADIS."""
    assert db.deve_avisar_dia_vazio("21/08/2026") is True    # sexta
    assert db.deve_avisar_dia_vazio("22/08/2026") is True    # sabado: a clinica abre
