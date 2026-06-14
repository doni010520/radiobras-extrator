"""
planos.py — Registro dos planos/convênios.

Cada plano tem seu PRÓPRIO portal, login e fluxo de anexo. Hoje só o OdontoPrev
(REDE UNNA) está automatizado (handler="fechar_dia"). Os demais ficam registrados
e aparecem no dashboard como "Não configurado" até a automação de cada um ser
construída — aí é só marcar ativo=True e apontar o handler.

Para adicionar/ajustar a lista: edite PLANOS abaixo. 'slug' é o identificador
estável (usado no histórico/URLs); não mude depois de ter execuções gravadas.
"""

PLANOS = [
    {
        "slug": "odontoprev",
        "nome": "REDE UNNA / OdontoPrev",
        "ativo": True,
        "handler": "fechar_dia",        # automação real existente
        "portal": "credenciado.odontoprev.com.br",
    },
    # ── Demais planos (a automação de cada um será construída depois) ──────────
    # Placeholders — substitua pelos nomes reais dos 20 planos.
    {"slug": "hapvida", "nome": "HAPVIDA ODONTO", "ativo": False, "handler": None},
    {"slug": "amil", "nome": "AMIL DENTAL", "ativo": False, "handler": None},
    {"slug": "porto", "nome": "PORTO SEGURO", "ativo": False, "handler": None},
    {"slug": "sulamerica", "nome": "SUL AMÉRICA", "ativo": False, "handler": None},
    {"slug": "unimed", "nome": "UNIMED ODONTO", "ativo": False, "handler": None},
    {"slug": "metlife", "nome": "METLIFE", "ativo": False, "handler": None},
]

_BY_SLUG = {p["slug"]: p for p in PLANOS}


def listar_planos() -> list:
    return PLANOS


def get_plano(slug: str) -> dict | None:
    return _BY_SLUG.get(slug)


def plano_ativo(slug: str) -> bool:
    p = _BY_SLUG.get(slug)
    return bool(p and p.get("ativo"))


def nome_plano(slug: str) -> str:
    p = _BY_SLUG.get(slug)
    return p["nome"] if p else slug
