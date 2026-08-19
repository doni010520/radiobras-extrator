"""DESFECHO — status na RedeUna das guias que NÓS faturamos (pago/glosado/
recursado/cancelado) + prazo de recurso. Lógica pura e determinística; a coleta
no portal fica em desfecho_extrator.py.

Janela de recurso (portal, tela Recurso de Glosa, 18/08/2026):
  ORTODONTIA: 120 dias a contar da data do repasse.
  DEMAIS:      90 dias a contar da data do repasse.
Vencido -> 'prescrito' (o portal mostra "Prazo ... prescrito").
"""
import re
from datetime import date, timedelta

# termos que caracterizam ortodontia (janela de 120 dias, não 90)
_ORTO_RE = re.compile(
    r"ortod[oôó]nt|cefalom[eé]tric|tra[çc]ado|documenta[çc][aã]o\s+ortod|"
    r"tele(?:rradiografia)?\s+com\s+tra|análise\s+cefal", re.I)

JANELA_ORTO = 120
JANELA_DEMAIS = 90


def eh_ortodontia(texto: str) -> bool:
    """True se o evento/exame é de ortodontia (define a janela de recurso)."""
    return bool(_ORTO_RE.search(texto or ""))


def prazo_recurso(data_repasse, ortodontia: bool, hoje: date) -> dict:
    """Prazo de recurso a partir da data do repasse.
    Retorna {data_limite, dias_restantes, prescrito}. Sem repasse -> indefinido
    (não dá pra contar o prazo antes do repasse sair no Demonstrativo)."""
    if data_repasse is None:
        return {"data_limite": None, "dias_restantes": None, "prescrito": False}
    janela = JANELA_ORTO if ortodontia else JANELA_DEMAIS
    data_limite = data_repasse + timedelta(days=janela)
    dias_restantes = (data_limite - hoje).days
    return {"data_limite": data_limite, "dias_restantes": dias_restantes,
            "prescrito": dias_restantes < 0}


def classificar_desfecho(cancelada: bool, demo) -> str:
    """Status financeiro primário de uma guia faturada por nós:
      CANCELADA   -> GTO cancelada / não autorizada (vence tudo).
      GLOSADA     -> Demonstrativo mostra valor glosado > 0.
      PAGA        -> Demonstrativo processado, pago e sem glosa.
      AGUARDANDO  -> repasse ainda não processado (sem dados no Demonstrativo)."""
    if cancelada:
        return "CANCELADA"
    if demo and demo.get("tem_dados"):
        if (demo.get("glosado") or 0) > 0:
            return "GLOSADA"
        if demo.get("pago"):
            return "PAGA"
    return "AGUARDANDO"
