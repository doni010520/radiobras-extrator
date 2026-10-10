"""Simulacao em DRY de dias passados com o codigo ATUAL (10/10).

Para cada conta/dia: roda a esteira em dry-run ignorando o que ja esta anexado no
portal (simular_sem_anexos), guarda os arquivos que ELA anexaria (plano_dir),
confere o CONTEUDO desses arquivos (mesmo veredito da auditoria_conteudo) e
compara com o que foi anexado de verdade naquele dia.

NUNCA anexa: rodar_esteira recusa simular_sem_anexos fora de dry_run.

Uso:  python simular_dias.py --dias 30 [--conta 397950] [--saida pasta]
      python simular_dias.py --dia 15/09/2026 --conta 388336
"""
import argparse
import datetime as dt
import json
import os
import re
import tempfile


def _mime(nome):
    n = nome.lower()
    if n.endswith(".pdf"):
        return "application/pdf"
    if n.endswith(".png"):
        return "image/png"
    return "image/jpeg"


def _norm_arq(n):
    """Nome comparavel entre rodadas (o indice de SOLICITACAO_<i>__ muda)."""
    return re.sub(r"^SOLICITACAO_\d+__", "SOLICITACAO_", os.path.basename(str(n)).strip())


def comparar(sim, real):
    """Diferenca entre o plano simulado e o anexado de verdade (so LAUDO/SOLICITACAO;
    ENTREGA_<hash> muda a cada geracao)."""
    chave = lambda ns: {_norm_arq(n) for n in ns  # noqa: E731
                        if re.match(r"(LAUDO|SOLICITACAO)_", _norm_arq(n))}
    a, b = chave(sim), chave(real)
    return {"so_simulacao": sorted(a - b), "so_real": sorted(b - a)}


def anexados_reais(gtos):
    """gto -> o que o robo de fato anexou (ultima rodada real que faturou)."""
    import db
    from sqlalchemy import text
    if not gtos:
        return {}
    sql = """select distinct on (i.gto) i.gto, i.arquivos_plano, i.categoria
             from execucao_itens i join execucoes x on x.id = i.execucao_id
             where i.gto = any(:g) and x.dry_run = false and i.faturado
               and i.categoria in ('auto','justificativa')
             order by i.gto, x.criado_em desc"""
    with db.engine.connect() as c:
        return {r["gto"]: {"arquivos": [a.strip() for a in (r["arquivos_plano"] or "").split(",")
                                        if a.strip()], "categoria": r["categoria"]}
                for r in c.execute(text(sql), {"g": list(gtos)}).mappings()}


def simular(conta, dia, saida, gem, log=print):
    import db
    import esteira
    from auditoria_conteudo import _ler, veredito

    plano_dir = os.path.join(saida, "planos", f"{conta}_{dia.replace('/', '')}")
    os.makedirs(plano_dir, exist_ok=True)
    r = esteira.rodar_esteira(dia, 1, 1, 1, log=lambda m: None,
                              gemini_key=os.environ["GEMINI_API_KEY"],
                              review_dir=tempfile.mkdtemp(), k_attach=1, dry_run=True,
                              conta=conta, senha_portal=db.get_portal_senha(conta),
                              simular_sem_anexos=True, plano_dir=plano_dir)
    decs = r.get("decisoes") or []
    reais = anexados_reais([d["gto"] for d in decs])
    out = []
    for d in decs:
        reg = {"conta": conta, "dia": dia, "gto": d["gto"], "paciente": d.get("paciente"),
               "categoria_sim": d.get("categoria"), "anexado_sim": d.get("anexado"),
               "arquivos_sim": d.get("arquivos_anexados") or [],
               "real": reais.get(d["gto"])}
        if reg["real"]:
            reg["diferenca"] = comparar(reg["arquivos_sim"], reg["real"]["arquivos"])
        pd = os.path.join(plano_dir, str(d["gto"]))
        if d.get("anexado") == "DRY" and os.path.isdir(pd):
            blobs = [(open(os.path.join(pd, f), "rb").read(), _mime(f))
                     for f in sorted(os.listdir(pd))]
            try:
                leit = _ler(gem, blobs) if blobs else []
                ref = {"dentista": d.get("dentista_gto"), "texto": d.get("gto_texto"),
                       "campo_49": d.get("justificativa")}
                ex = d.get("gto_exames_desta") or d.get("gto_exames") or []
                reg["status"], reg["motivos"] = veredito(
                    d.get("paciente"), " ".join(ex), leit,
                    "justificativa" if d.get("categoria") == "justificativa" else None,
                    gto_ref=ref)
                reg["leituras"] = leit
            except Exception as e:
                reg["status"], reg["motivos"] = "NAO_CONFERIDA", [str(e)[:160]]
        else:
            reg["status"], reg["motivos"] = "NAO_ANEXARIA", [
                str(d.get("anexar_erro") or (d.get("gemini") or {}).get("motivo")
                    or d.get("erro") or "")[:200]]
        out.append(reg)
        dif = reg.get("diferenca") or {}
        log(f"[{conta} {dia}] {d['gto']} {str(d.get('paciente'))[:26]:26} "
            f"{reg['status']:13} real={'sim' if reg['real'] else 'nao'}"
            + (f" | +{dif['so_simulacao']} -{dif['so_real']}"
               if dif.get("so_simulacao") or dif.get("so_real") else "")
            + (f" | {'; '.join(reg['motivos'])[:140]}" if reg["status"] != "OK" else ""))
    return out


def dias_uteis(n, ate=None):
    ate = ate or (dt.date.today() - dt.timedelta(days=1))
    return [(ate - dt.timedelta(days=i)).strftime("%d/%m/%Y") for i in range(n)
            if (ate - dt.timedelta(days=i)).weekday() < 6]      # seg-sab


if __name__ == "__main__":
    from google import genai
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=0)
    ap.add_argument("--dia", action="append", default=[])
    ap.add_argument("--conta", action="append", default=[])
    ap.add_argument("--saida", default=".")
    a = ap.parse_args()
    contas = a.conta or ["388336", "397950", "410923"]
    dias = a.dia or dias_uteis(a.dias)
    gem = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    arq = os.path.join(a.saida, "simulacao.jsonl")
    feitos = set()
    if os.path.exists(arq):
        for ln in open(arq, encoding="utf-8"):
            try:
                x = json.loads(ln)
                feitos.add((x["conta"], x["dia"]))
            except Exception:
                pass
    for dia in dias:
        for ct in contas:
            if (ct, dia) in feitos:
                continue
            try:
                regs = simular(ct, dia, a.saida, gem)
            except Exception as e:
                print(f"[{ct} {dia}] FALHOU: {str(e)[:200]}", flush=True)
                continue
            with open(arq, "a", encoding="utf-8") as f:
                for reg in regs:
                    f.write(json.dumps(reg, ensure_ascii=False) + "\n")
            print(f"[{ct} {dia}] {len(regs)} guias", flush=True)
