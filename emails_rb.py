"""E-mails da RadioBras (formato aprovado pelo dono em 15/10/2026).

1. Prazo (diario): so as guias que alguem PRECISA agir ate vencer, separadas por quem
   age, uma linha por guia com o que fazer. Falha nossa fica de fora (vai por WhatsApp
   ao dono e a clinica nao deve ver). Vencidas viram UMA linha no fim.
2. Resumo da semana: contado POR GUIA (nao por execucao), por unidade, com o que o robo
   faturou, o que ja tinha documento de outra pessoa e o que ficou pendente.

Funcoes puras de montagem (testaveis) + coletas no banco.
"""
from collections import Counter, defaultdict, OrderedDict
from html import escape

ORDEM_QUEM = ["Clínica", "Radiologista", "Cadastro", "Conferência", "Clínica + Radiologista"]
NOME_QUEM = {"Radiologista": "Radiologia"}


def _quem(q):
    return NOME_QUEM.get(q, q)


def _rotulo_prazo(sla):
    return "vence hoje" if sla <= 0 else ("vence amanhã" if sla == 1 else f"faltam {sla} dias")


# ── 1. prazo ────────────────────────────────────────────────────────────────

def montar_email_prazo(pendencias, sla_fn, unidade_fn, classificar, eh_nosso, janela=2):
    """(assunto, txt, html) ou (None, None, None) se ninguem precisa agir."""
    urgentes, vencidas = [], 0
    for p in pendencias or []:
        if eh_nosso(p.get("motivo"), p.get("categoria")):
            continue
        sla = sla_fn(p.get("dia"))
        if sla is None:
            continue
        if sla < 0:
            vencidas += 1
            continue
        if sla <= janela:
            chave, quem, acao = classificar(p.get("motivo"), p.get("categoria"))[:3]
            urgentes.append(dict(p, sla=sla, quem=quem, acao=acao,
                                 unidade=unidade_fn(p.get("conta"))))
    if not urgentes:
        return None, None, None
    grupos = OrderedDict()
    for q in sorted({u["quem"] for u in urgentes},
                    key=lambda q: ORDEM_QUEM.index(q) if q in ORDEM_QUEM else 99):
        grupos[q] = sorted([u for u in urgentes if u["quem"] == q], key=lambda u: u["sla"])
    cont = ", ".join(f"{_quem(q)} {len(v)}" for q, v in grupos.items())
    assunto = f"RadioBras: {len(urgentes)} guia(s) vencem até {'depois de amanhã' if janela >= 2 else 'amanhã'} ({cont})"

    txt = [f"{len(urgentes)} guia(s) ainda não faturada(s) e perto do prazo de 7 dias da OdontoPrev.", ""]
    html = [f"<div style='font-family:Arial,sans-serif;max-width:720px'>"
            f"<h2 style='margin:0 0 6px'>{escape(assunto)}</h2>"
            f"<p style='color:#555;margin:0 0 14px'>Guias ainda não faturadas e perto do prazo "
            f"de 7 dias da OdontoPrev. Depois do prazo, só por recurso.</p>"]
    for q, itens in grupos.items():
        txt.append(f"== {_quem(q).upper()} ({len(itens)})")
        txt.append(f"   O que fazer: {itens[0]['acao']}")
        html.append(f"<h3 style='margin:16px 0 4px'>{escape(_quem(q))} ({len(itens)})</h3>"
                    f"<p style='margin:0 0 6px;color:#333'><b>O que fazer:</b> {escape(itens[0]['acao'])}</p>"
                    f"<table style='border-collapse:collapse;font-size:13px'>")
        for u in itens:
            txt.append(f"   • {_rotulo_prazo(u['sla'])} · {u.get('paciente') or '—'} · guia {u.get('gto')} "
                       f"· {u['unidade']} · exame {u.get('dia')}")
            cor = "#b3261e" if u["sla"] <= 1 else "#9a4d00"
            html.append(f"<tr><td style='padding:3px 10px 3px 0;color:{cor};font-weight:700;white-space:nowrap'>"
                        f"{_rotulo_prazo(u['sla'])}</td>"
                        f"<td style='padding:3px 10px 3px 0'>{escape(u.get('paciente') or '—')}</td>"
                        f"<td style='padding:3px 10px 3px 0'>guia {escape(str(u.get('gto')))}</td>"
                        f"<td style='padding:3px 10px 3px 0;color:#555'>{escape(u['unidade'])} · exame {escape(str(u.get('dia')))}</td></tr>")
        html.append("</table>")
        txt.append("")
    if vencidas:
        txt.append(f"Além disso, {vencidas} guia(s) já passaram do prazo e só entram por recurso (ver painel).")
        html.append(f"<p style='margin-top:18px;color:#555'>Além disso, <b>{vencidas}</b> guia(s) já passaram "
                    f"do prazo e só entram por recurso (ver o painel de pendências).</p>")
    html.append("</div>")
    return assunto, "\n".join(txt), "".join(html)


# ── 2. resumo da semana ─────────────────────────────────────────────────────

def classificar_guias(linhas):
    """linhas: [{gto, conta, categoria, faturado}] de execucoes reais. Um destino por guia:
    'robo_pedido' | 'robo_justificativa' | 'outra_pessoa' | 'nao_faturada'."""
    por = defaultdict(list)
    for l in linhas or []:
        por[(l["conta"], str(l["gto"]))].append(l)
    out = {}
    for k, ls in por.items():
        fat = [l for l in ls if l.get("faturado")]
        if any(l["categoria"] == "auto" for l in fat):
            out[k] = "robo_pedido"
        elif any(l["categoria"] == "justificativa" for l in fat):
            out[k] = "robo_justificativa"
        elif fat:
            out[k] = "outra_pessoa"
        else:
            out[k] = "nao_faturada"
    return out


def montar_resumo_semana(destinos, pendencias, unidade_fn, sla_fn, classificar, eh_nosso,
                         inicio, fim):
    """destinos: {(conta, gto): destino}. (assunto, txt, html) ou (None, None, None)."""
    if not destinos:
        return None, None, None
    por_un = defaultdict(Counter)
    for (conta, _g), d in destinos.items():
        por_un[unidade_fn(conta)][d] += 1
    pend_quem, nossas, vencidas = Counter(), 0, 0
    for p in pendencias or []:
        if eh_nosso(p.get("motivo"), p.get("categoria")):
            nossas += 1
            continue
        sla = sla_fn(p.get("dia"))
        if sla is not None and sla < 0:
            vencidas += 1
            continue
        pend_quem[_quem(classificar(p.get("motivo"), p.get("categoria"))[1])] += 1
    fat = sum(v["robo_pedido"] + v["robo_justificativa"] for v in por_un.values())
    pend = sum(pend_quem.values())
    assunto = (f"RadioBras: semana {inicio} a {fim}, {fat} guia(s) faturada(s) pelo robô, "
               f"{pend} pendente(s)")
    rot = [("robo_pedido", "Faturadas pelo robô (com pedido)"),
           ("robo_justificativa", "Faturadas pelo robô (justificativa campo 49)"),
           ("outra_pessoa", "Já tinham documentos de outra pessoa"),
           ("nao_faturada", "Não faturadas na semana")]
    txt = [assunto, ""]
    html = [f"<div style='font-family:Arial,sans-serif;max-width:720px'><h2 style='margin:0 0 10px'>"
            f"{escape(assunto)}</h2><table style='border-collapse:collapse;font-size:13px'>"
            f"<tr style='background:#f2f2f2'><th style='text-align:left;padding:5px 10px'>Unidade</th>"
            + "".join(f"<th style='padding:5px 10px'>{escape(r)}</th>" for _, r in rot) + "</tr>"]
    for un in sorted(por_un):
        v = por_un[un]
        txt.append(f"{un}: " + " · ".join(f"{r.lower()} {v[k]}" for k, r in rot))
        html.append(f"<tr><td style='padding:5px 10px'>{escape(un)}</td>"
                    + "".join(f"<td style='padding:5px 10px;text-align:center'>{v[k]}</td>" for k, _ in rot)
                    + "</tr>")
    html.append("</table>")
    txt += ["", "Pendentes agora (no prazo), por quem precisa agir:"]
    html.append("<h3 style='margin:16px 0 4px'>Pendentes agora (no prazo)</h3><ul style='margin:0'>")
    for q in sorted(pend_quem, key=lambda q: -pend_quem[q]):
        txt.append(f"  • {q}: {pend_quem[q]}")
        html.append(f"<li>{escape(q)}: <b>{pend_quem[q]}</b></li>")
    if not pend_quem:
        txt.append("  • nenhuma")
        html.append("<li>nenhuma</li>")
    html.append("</ul>")
    txt += ["", f"Vencidas (só recurso): {vencidas}",
            f"Falhas técnicas nossas abertas (o robô re-tenta; avisadas por WhatsApp): {nossas}"]
    html.append(f"<p style='margin-top:14px'>Vencidas (só recurso): <b>{vencidas}</b><br>"
                f"Falhas técnicas nossas abertas (o robô re-tenta; avisadas por WhatsApp): "
                f"<b>{nossas}</b></p></div>")
    return assunto, "\n".join(txt), "".join(html)


def linhas_da_semana(dias=7):
    """Itens das execucoes REAIS (OdontoPrev) criadas nos ultimos `dias`."""
    import db
    from sqlalchemy import text
    sql = """select i.gto, x.conta, i.categoria, i.faturado
             from execucao_itens i join execucoes x on x.id = i.execucao_id
             where x.criado_em >= now() - make_interval(days => :d)
               and x.dry_run = false and x.conta not like 'hapvida:%'"""
    with db.engine.connect() as c:
        return [dict(r) for r in c.execute(text(sql), {"d": dias}).mappings()]
