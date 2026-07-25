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
# pras 3). Cada conta tem seus convênios no PRORADIS. A ordem aqui é a da lista
# do seletor.
#
# segmentos = [] significa NÃO RESTRINGIR por segmento no relatório analítico.
# O convênio já identifica a unidade, então o segmento é redundante — e em
# Camaçari era nocivo: os exames de lá são registrados com segmento diferente de
# "CAMAÇARI" e sumiam do analítico, virando pendência FALSA ("exame não
# encontrado no PRORADIS") mesmo estando registrados no convênio certo.
# Medição em 15-18/07 (soltando o segmento): Centro +0, Tancredo +0,
# Camaçari +22 pacientes recuperados. Por isso vale para as três.
PLANOS = {
    "388336": {
        "label": "RedeUna — Centro, Lauro, Periperi e Itaigara",
        "convenios": ["REDE UNNA - CENTRO", "REDE UNNA - ITAIGARA",
                      "REDE UNNA - PERIPERI", "REDE UNNA - LAURO DE FREITAS"],
        "segmentos": [],
    },
    "397950": {
        "label": "RedeUna — Tancredo",
        "convenios": ["REDE UNNA CAMINHO DAS ÁRVORES - TANCREDO"],
        "segmentos": [],
    },
    "410923": {
        "label": "RedeUna — Camaçari",
        "convenios": ["REDE UNNA - CAMAÇARI", "REDE UNNA DESCONTO CAMAÇARI"],
        "segmentos": [],
    },
}

# Outros planos/operadoras ainda não automatizados — aparecem no seletor mas
# DESABILITADOS (não selecionáveis). Edite a lista conforme forem integrados.
PLANOS_INATIVOS = [
    "Outros planos — em breve",
]
