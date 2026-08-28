"""Auditoria de COBERTURA das guias que o robo faturou.

Pergunta que a esteira nunca fez antes de anexar: o que foi entregue cobre os
exames que a guia autoriza? A guarda `_entregavel_faltando` so pergunta "existe
algum LAUDO_*" — por isso a PALOMA (195670786, 31/07) faturou com dois laudos e
ZERO imagem e voltou GLOSADA 3230, e a ANA CRISTINA (196322294, 18/08) faturou com
o laudo da panoramica e uma folha que nao tinha a panoramica.

SO LEITURA. Nao anexa, nao muda pendencia, nao decide nada — monta a lista para a
clinica conferir enquanto as guias ainda estao AGUARDANDO repasse.

Uso:  python _auditoria_cobertura.py [--desde DD/MM/AAAA]
"""
import io
import os
import sys

from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import text                                    # noqa: E402

import db                                                      # noqa: E402
from config import PLANOS                                      # noqa: E402
from esteira import _exames_sem_laudo, _sem_imagem_no_plano     # noqa: E402
from solicitacao_utils import canon_exames                     # noqa: E402


def _unidade(conta):
    return (PLANOS.get(str(conta)) or {}).get("label", str(conta))


def _dia_key(d):
    p = (d or "").split("/")
    return (p[2], p[1], p[0]) if len(p) == 3 else ("", "", "")


def auditar(desde=None):
    """[(bloco, dia, conta, gto, paciente, pede, faltando, status)] — bloco e
    'SEM_IMAGEM' ou 'SEM_LAUDO'."""
    achados = []
    with db.engine.connect() as c:
        lote = c.execute(text("select max(lote) from guia_desfechos")).scalar()
        desfecho = {r[0]: r[1] for r in c.execute(
            text("select gto, status from guia_desfechos where lote=:l"), {"l": lote})}
        linhas = c.execute(text("""
            select distinct on (i.gto) i.gto, i.paciente, x.dia, x.conta,
                   coalesce(i.exames_gto,'') eg, coalesce(i.arquivos_plano,'') plano
            from execucao_itens i join execucoes x on x.id = i.execucao_id
            where i.faturado and i.categoria in ('auto','justificativa')
              and coalesce(i.arquivos_plano,'') <> ''
            order by i.gto, x.criado_em desc""")).mappings().all()
    for r in linhas:
        if desde and _dia_key(r["dia"]) < _dia_key(desde):
            continue
        exames = canon_exames(r["eg"])          # texto digitado: sem `recuperar`
        if not exames:
            continue                            # guia sem exame legivel: outro problema
        plano = [a.strip() for a in r["plano"].split(",") if a.strip()]
        st = desfecho.get(r["gto"], "SEM_DESFECHO")
        base = (r["dia"], r["conta"], r["gto"], r["paciente"], r["eg"], st)
        if _sem_imagem_no_plano(exames, plano):
            achados.append(("SEM_IMAGEM", *base[:4], base[4], "nenhuma imagem anexada", st))
            continue                            # nao repete a mesma guia nos dois blocos
        faltam = _exames_sem_laudo(exames, plano, apenas_esperados=True)
        if faltam:
            achados.append(("SEM_LAUDO", *base[:4], base[4],
                            "sem laudo de: " + ", ".join(sorted(faltam)), st))
    achados.sort(key=lambda a: (a[0], _dia_key(a[1]), a[2]))
    return achados


def relatorio(achados) -> str:
    L = []
    sem_img = [a for a in achados if a[0] == "SEM_IMAGEM"]
    sem_lau = [a for a in achados if a[0] == "SEM_LAUDO"]
    L.append("AUDITORIA DE COBERTURA — guias que o robo faturou")
    L.append("")
    L.append(f"1) FATURADAS SEM NENHUMA IMAGEM: {len(sem_img)}")
    L.append("   Mesma cara da PALOMA, que voltou glosada 3230.")
    for _, dia, conta, gto, pac, pede, falta, st in sem_img:
        L.append(f"   {dia} | {_unidade(conta)} | GTO {gto} | {pac}")
        L.append(f"        guia pede: {pede} | desfecho: {st}")
    L.append("")
    L.append(f"2) FATURADAS COM EXAME SEM LAUDO: {len(sem_lau)}")
    L.append("   So panoramica/telerradiografia/tomografia, onde o laudo e a norma.")
    for _, dia, conta, gto, pac, pede, falta, st in sem_lau:
        L.append(f"   {dia} | {_unidade(conta)} | GTO {gto} | {pac}")
        L.append(f"        guia pede: {pede}")
        L.append(f"        {falta} | desfecho: {st}")
    return chr(10).join(L)


if __name__ == "__main__":
    _desde = None
    for a in sys.argv[1:]:
        if a.startswith("--desde="):
            _desde = a.split("=", 1)[1]
    _ach = auditar(_desde)
    _txt = relatorio(_ach)
    io.open("auditoria_cobertura.txt", "w", encoding="utf-8").write(_txt)
    print(_txt)
