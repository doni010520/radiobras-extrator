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
