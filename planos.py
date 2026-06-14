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
    # ── Plano automatizado ─────────────────────────────────────────────────────
    {
        "slug": "odontoprev",
        "nome": "REDE UNNA / OdontoPrev",
        "ativo": True,
        "handler": "fechar_dia",        # automação real existente
        "portal": "credenciado.odontoprev.com.br",
    },
    # ── Demais planos (automação a construir; aparecem como 'Não configurado') ──
    {"slug": "amil", "nome": "Amil Dental", "ativo": False, "handler": None},
    {"slug": "atemde", "nome": "Atemde", "ativo": False, "handler": None},
    {"slug": "hapvida_odonto", "nome": "Hapvida Odonto", "ativo": False, "handler": None},
    {"slug": "hapvida_saude", "nome": "Hapvida Saúde", "ativo": False, "handler": None},
    {"slug": "idental", "nome": "Idental", "ativo": False, "handler": None},
    {"slug": "metlife", "nome": "Metlife", "ativo": False, "handler": None},
    {"slug": "odonto_empresas", "nome": "Odonto Empresas", "ativo": False, "handler": None},
    {"slug": "odonto_sa", "nome": "Odonto SA", "ativo": False, "handler": None},
    {"slug": "odontosystem", "nome": "Odontosystem", "ativo": False, "handler": None},
    {"slug": "orale", "nome": "Orale", "ativo": False, "handler": None},
    {"slug": "petrobras", "nome": "Petrobras", "ativo": False, "handler": None},
    {"slug": "plano_clin", "nome": "Plano Clin Digital", "ativo": False, "handler": None},
    {"slug": "porto", "nome": "Porto Seguro", "ativo": False, "handler": None},
    {"slug": "qualidonto", "nome": "Qualidonto", "ativo": False, "handler": None},
    {"slug": "servdonto", "nome": "Servdonto", "ativo": False, "handler": None},
    {"slug": "sulamerica", "nome": "Sul América", "ativo": False, "handler": None},
    {"slug": "totalis", "nome": "Totalis", "ativo": False, "handler": None},
    {"slug": "unimed", "nome": "Unimed Odonto", "ativo": False, "handler": None},
    {"slug": "uniodonto", "nome": "Uniodonto", "ativo": False, "handler": None},
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
