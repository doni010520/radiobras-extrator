"""Toca os cassetes e compara o veredito com baseline.json.
Uso: python3 replay_comparar.py            -> compara e imprime diferencas
     python3 replay_comparar.py --baseline -> grava a linha de base atual"""
import json
import os
import sys
import tempfile

import replay_harness as rh


def tocar_um(pasta_cassete: str) -> dict:
    import esteira
    c = rh.Cassete(pasta_cassete)
    m = c.meta
    with rh.instalar("tocar", c):
        r = esteira.rodar_esteira(m["dia"], 1, 1, 5, log=lambda s: None,
                                  gemini_key="replay", review_dir=tempfile.mkdtemp(),
                                  k_attach=1, dry_run=True, conta=m["conta"],
                                  senha_portal="replay")
    return rh.veredito(r)


def tocar_todos(pasta: str = rh.PASTA_PADRAO) -> dict:
    out = {}
    for nome in sorted(os.listdir(pasta)):
        p = os.path.join(pasta, nome)
        if os.path.isfile(os.path.join(p, "chamadas.json")):
            out[nome] = tocar_um(p)
    return out


def diferencas(base: dict, atual: dict) -> list:
    dif = []
    for cas in sorted(set(base) | set(atual)):
        b, a = base.get(cas, {}), atual.get(cas, {})
        for g in sorted(set(b) | set(a)):
            if g not in a:
                dif.append(f"{cas} gto {g}: sumiu da rodada")
                continue
            if g not in b:
                dif.append(f"{cas} gto {g}: apareceu na rodada")
                continue
            for campo in sorted(set(b[g]) | set(a[g])):
                if b[g].get(campo) != a[g].get(campo):
                    dif.append(f"{cas} gto {g}: {campo} {b[g].get(campo)} -> {a[g].get(campo)}")
    return dif


if __name__ == "__main__":
    atual = tocar_todos()
    arq = os.path.join(rh.PASTA_PADRAO, "baseline.json")
    if "--baseline" in sys.argv:
        json.dump(atual, open(arq, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"linha de base gravada: {sum(len(v) for v in atual.values())} guias "
              f"em {len(atual)} cassete(s)")
        sys.exit(0)
    dif = diferencas(json.load(open(arq, encoding="utf-8")), atual)
    print("\n".join(dif) if dif else "IGUAL: nenhuma decisao mudou")
    sys.exit(1 if dif else 0)
