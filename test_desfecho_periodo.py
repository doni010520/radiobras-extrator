"""Demonstrativo por período (API) -> desfecho por guia (18/10)."""
import desfecho as _d
from desfecho_extrator import ler_periodo


def _ev(lib, status="A", glosa=0.0, data="2026-09-03", cod=None):
    e = {"codStatusEvento": status, "valorLiberado": lib, "valorEventoGlosa": glosa,
         "dataPagamento": data if lib > 0 else None}
    if cod:
        e["pagamentoGlosadoDTO"] = {"glosa": {"codigo": cod, "descricao": "Documentação incompleta"}}
    return e


def _j(*fichas):
    return {"pesquisaFichasDentista": [{"fichasConsolidadas": [
        {"nrFicha": n, "eventosValores": evs} for n, evs in fichas]}]}


def test_paga_glosada_e_cancelada():
    r = ler_periodo(_j((1, [_ev(29.0), _ev(5.5)]),
                       (2, [_ev(0.0, status="G", cod=3052)]),          # negado com valor 0
                       (3, [{"codStatusEvento": "CANCELADO", "valorLiberado": 0}])))
    assert _d.classificar_desfecho(False, r["1"]) == "PAGA"
    assert r["1"]["data_repasse"] == "03/09/2026" and r["1"]["liberado"] == 34.5
    assert r["2"]["negado"] and r["2"]["glosado"] == 0.0 and r["2"]["glosa_cod"] == "3052"
    assert "3" not in r


def test_parcial_tem_glosa_e_pagamento():
    r = ler_periodo(_j((7, [_ev(29.0), _ev(0.0, status="G", glosa=5.54, cod=3052)])))
    assert r["7"]["pago"] and r["7"]["negado"] and r["7"]["glosado"] == 5.54
    assert _d.classificar_desfecho(False, r["7"]) == "GLOSADA"
