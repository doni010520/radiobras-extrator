"""Configuração de escopo REDE UNNA (convênios/segmentos).

Módulo separado para evitar import circular: tanto o app.py quanto os módulos
de extração importam estas constantes daqui — nunca de app.py.

As unidades comentadas (CAMAÇARI, TANCREDO, DESCONTO) ficam fáceis de reativar.
"""

CONVENIOS = [
    "REDE UNNA - CENTRO",
    "REDE UNNA - ITAIGARA",
    "REDE UNNA - PERIPERI",
    "REDE UNNA - LAURO DE FREITAS",
    # "REDE UNNA - CAMAÇARI",
    # "REDE UNNA CAMINHO DAS ÁRVORES - TANCREDO",
    # "REDE UNNA DESCONTO CAMAÇARI",
]

SEGMENTOS = ["CENTRO", "ITAIGARA", "LAURO", "PERIPERI"]

# Planos = cada conta RedeUna no OdontoPrev (login = código da conta; mesma senha
# pras 3). Cada conta tem seus convênios/segmentos no PRORADIS. A ordem aqui é a
# da lista do seletor.
PLANOS = {
    "388336": {
        "label": "RedeUna — Centro, Lauro, Periperi e Itaigara",
        "convenios": ["REDE UNNA - CENTRO", "REDE UNNA - ITAIGARA",
                      "REDE UNNA - PERIPERI", "REDE UNNA - LAURO DE FREITAS"],
        "segmentos": ["CENTRO", "ITAIGARA", "LAURO", "PERIPERI"],
    },
    "397950": {
        "label": "RedeUna — Tancredo",
        "convenios": ["REDE UNNA CAMINHO DAS ÁRVORES - TANCREDO"],
        "segmentos": ["TANCREDO"],
    },
    "410923": {
        "label": "RedeUna — Camaçari",
        "convenios": ["REDE UNNA - CAMAÇARI", "REDE UNNA DESCONTO CAMAÇARI"],
        "segmentos": ["CAMAÇARI"],
    },
}

# Outros planos/operadoras ainda não automatizados — aparecem no seletor mas
# DESABILITADOS (não selecionáveis). Edite a lista conforme forem integrados.
PLANOS_INATIVOS = [
    "Outros planos — em breve",
]
