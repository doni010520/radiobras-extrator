"""Cérebro da aba DESFECHO (status na RedeUna das guias que NÓS faturamos).

Regras vindas do portal (recon 18/08/2026, tela Recurso de Glosa):
  "recursos de glosa em até 120 dias a contar da data do repasse para ORTODONTIA
   e em até 90 dias para as demais especialidades" -> vencido = 'prescrito'.

Fontes por guia:
  Demonstrativo de Pagamento -> pago / glosado (data do repasse)
  GTO cancelada/não autorizada -> CANCELADA
  Relatório de Glosa -> motivo + como recursar
"""
from datetime import date

from desfecho import eh_ortodontia, prazo_recurso, classificar_desfecho


# ── eh_ortodontia: decide a janela 120 x 90 dias ────────────────────────────────
def test_documentacao_ortodontica_e_orto():
    assert eh_ortodontia("Documentação ortodôntica completa: panorâmica, tele com traçado") is True


def test_analise_cefalometrica_e_orto():
    assert eh_ortodontia("Análise cefalométrica") is True


def test_periapical_nao_e_orto():
    assert eh_ortodontia("Radiografia periapical") is False


def test_panoramica_simples_nao_e_orto():
    assert eh_ortodontia("Radiografia panorâmica") is False


# ── prazo_recurso ───────────────────────────────────────────────────────────────
def test_prazo_orto_120_dias():
    r = prazo_recurso(date(2026, 6, 1), ortodontia=True, hoje=date(2026, 6, 30))
    assert r["data_limite"] == date(2026, 9, 29)      # 1/jun + 120d
    assert r["dias_restantes"] == 91
    assert r["prescrito"] is False


def test_prazo_demais_90_dias():
    r = prazo_recurso(date(2026, 6, 1), ortodontia=False, hoje=date(2026, 6, 30))
    assert r["data_limite"] == date(2026, 8, 30)      # 1/jun + 90d
    assert r["prescrito"] is False


def test_prazo_prescrito_quando_vencido():
    r = prazo_recurso(date(2026, 1, 1), ortodontia=False, hoje=date(2026, 8, 18))
    assert r["prescrito"] is True
    assert r["dias_restantes"] < 0


def test_prazo_sem_repasse_fica_indefinido():
    r = prazo_recurso(None, ortodontia=False, hoje=date(2026, 8, 18))
    assert r["data_limite"] is None
    assert r["dias_restantes"] is None
    assert r["prescrito"] is False


# ── classificar_desfecho: status financeiro primário ────────────────────────────
def test_cancelada_vence_tudo():
    demo = {"tem_dados": True, "pago": True, "glosado": 0.0}
    assert classificar_desfecho(cancelada=True, demo=demo) == "CANCELADA"


def test_paga_quando_demo_pago_sem_glosa():
    demo = {"tem_dados": True, "pago": True, "glosado": 0.0}
    assert classificar_desfecho(cancelada=False, demo=demo) == "PAGA"


def test_glosada_quando_ha_valor_glosado():
    demo = {"tem_dados": True, "pago": True, "glosado": 42.5}
    assert classificar_desfecho(cancelada=False, demo=demo) == "GLOSADA"


def test_aguardando_quando_demo_sem_dados():
    demo = {"tem_dados": False, "pago": False, "glosado": None}
    assert classificar_desfecho(cancelada=False, demo=demo) == "AGUARDANDO"


def test_aguardando_quando_demo_ausente():
    assert classificar_desfecho(cancelada=False, demo=None) == "AGUARDANDO"
