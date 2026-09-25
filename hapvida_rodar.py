"""
hapvida_rodar.py — Roda o faturamento Hapvida de um dia, por linha de comando.

  python hapvida_rodar.py 08/09/2026 centro             # SIMULAÇÃO (padrão)
  python hapvida_rodar.py 08/09/2026 centro --guias 28372430 28295932
  python hapvida_rodar.py 08/09/2026 centro --sem-proradis   # só portal: lista + o que já tem imagem

Não grava no banco e não anexa nada. O relatório sai no formato de sempre:
seria faturado / não seria / por quê.
"""
from __future__ import annotations

import argparse
import sys

import hapvida_fluxo as hf
from hapvida_portal import PortalHapvida


class _SemProradis:
    def buscar(self, **kw):
        return {"encontrado": False}


def relatorio(resumo: dict) -> str:
    dec = resumo["decisoes"]
    ja = [d for d in dec if d["categoria"] == "ja_anexada"]
    fat = [d for d in dec if d["anexado"] in ("OK", "SIMULADO") and d not in ja]
    nao = [d for d in dec if d not in fat and d not in ja]
    out = [f"HAPVIDA {resumo['conta']} — {resumo['data']} — "
           f"{'SIMULAÇÃO' if resumo['dry_run'] else 'REAL'}",
           f"{len(dec)} guias: {len(fat)} seriam faturadas, {len(ja)} já tinham imagem, {len(nao)} não seriam", ""]
    out.append(f"SERIA FATURADO ({len(fat)})")
    out += [f"  - {d['gto']} {d['paciente']} [{', '.join(d['gto_exames'])}] -> {', '.join(d.get('arquivos_anexados') or [])}"
            for d in fat] or ["  (nenhuma)"]
    out.append(f"\nJÁ TINHA IMAGEM ({len(ja)})")
    out += [f"  - {d['gto']} {d['paciente']}" for d in ja] or ["  (nenhuma)"]
    out.append(f"\nNÃO SERIA FATURADO ({len(nao)}) — POR QUÊ")
    out += [f"  - {d['gto']} {d['paciente']} [{', '.join(d['gto_exames'])}] "
            f"({(d.get('gemini') or {}).get('responsavel') or '?'}): "
            f"{(d.get('gemini') or {}).get('motivo') or d.get('erro') or d.get('anexar_erro') or d['categoria']}"
            for d in nao] or ["  (nenhuma)"]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dia")
    ap.add_argument("unidade")
    ap.add_argument("--guias", nargs="*")
    ap.add_argument("--sem-proradis", action="store_true")
    a = ap.parse_args(argv)

    portal = PortalHapvida(a.unidade)
    if a.sem_proradis:
        r = hf.rodar_hapvida(a.dia, a.unidade, portal=portal, fonte=_SemProradis(),
                             dry_run=True, apenas_guias=a.guias)
    else:
        from hapvida_proradis import FonteProradis
        with FonteProradis() as fonte:
            r = hf.rodar_hapvida(a.dia, a.unidade, portal=portal, fonte=fonte,
                                 dry_run=True, apenas_guias=a.guias)
    print("\n" + relatorio(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
