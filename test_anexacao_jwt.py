"""GAP (achado 17/08): a anexação falha com 'Jwt is expired' (token OdontoPrev
expirou no meio da rodada longa) -> motivo 'anexação falhou' -> classe_retry='logica'
-> o retry loop NÃO reprocessa, apesar de ser o transitório mais clássico. Fix:
JWT expirado / falha ao contar anexos = transitorio. Sem afrouxar o caso de laudo
trocado (dado, não transitório)."""
from db import classe_retry


def test_anexacao_falhou_por_jwt_expirado_e_transitorio():
    m = ("Documentação OK, mas a anexação falhou: nao consegui ler quantos anexos a guia "
         "ja tem (DOM e API falharam: HTTP 401 'Jwt is expired') — nada foi enviado, por seguranca")
    assert classe_retry(m, "auto") == "transitorio"


def test_anexacao_falhou_por_contagem_de_anexos_e_transitorio():
    m = ("Documentação OK, mas a anexação falhou: nao consegui ler quantos anexos a guia "
         "ja tem apos 3 tentativas — nada foi enviado, por seguranca")
    assert classe_retry(m, "auto") == "transitorio"


def test_anexacao_falhou_por_laudo_trocado_NAO_e_transitorio():
    # dado errado (laudo de outro exame) -> conferencia humana, nunca retry cego
    m = ("Documentação OK, mas a anexação falhou: a guia pede panorâmica, mas os laudos "
         "encontrados eram de tomografia — de outro exame do mesmo dia.")
    assert classe_retry(m, "auto") != "transitorio"
