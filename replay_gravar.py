"""Grava cassetes de rodadas DRY reais (nunca anexa).
Uso: python3 replay_gravar.py 388336:07/10/2026 397950:07/10/2026 ...
Rodar nesta maquina, sem ODONTO_PROXY_URL, com GEMINI_API_KEY e SMARTRIS_* no ambiente."""
import os
import sys
import tempfile

import replay_harness as rh


def gravar(conta: str, dia: str) -> str:
    import db
    import esteira
    nome = f"{conta}_{dia.replace('/', '')}"
    c = rh.Cassete(os.path.join(rh.PASTA_PADRAO, nome))
    c.meta.update({"conta": conta, "dia": dia})
    with rh.instalar("gravar", c):
        r = esteira.rodar_esteira(dia, 1, 1, 5, log=print,
                                  gemini_key=os.environ["GEMINI_API_KEY"],
                                  review_dir=tempfile.mkdtemp(), k_attach=1,
                                  dry_run=True, conta=conta,
                                  senha_portal=db.get_portal_senha(conta))
    print(f"[gravado] {nome}: {len(r.get('decisoes') or [])} decisao(oes)")
    return nome


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        conta, dia = arg.split(":", 1)
        gravar(conta, dia)
