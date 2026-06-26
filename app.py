"""
RADIOBRAS — Extrator Web
Flask app: relatório analítico (xlsx) + download de arquivos (ZIP).
"""

import io
import logging
import os
import re
import sys
import threading
import traceback
import uuid
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    _TZ = None

from flask import Flask, jsonify, render_template, request, send_file

sys.path.insert(0, os.path.dirname(__file__))

from extrator_pacientes_analitico import (
    discover_tokens_and_cookies,
    get_credentials,
    parse_html_to_df,
    post_relatorio,
    resolve_tokens,
)
from extrator_arquivos import processar_dia
from ciclo_completo import ciclo_dia
from fechar_dia import fechar_dia
import db
import planos as planos_mod

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Escopo REDE UNNA — definido em config.py (evita import circular).
from config import CONVENIOS, SEGMENTOS, PLANOS, PLANOS_INATIVOS

# Inicializa o banco (cria tabelas se não existirem). Falha não derruba o app.
try:
    db.init_db()
    app.logger.info("Banco inicializado (%s).", db.DATABASE_URL.split("@")[-1])
    # Toda execução em 'running' no startup é zumbi (o processo que a iniciou
    # morreu) — marca como erro. SÓ sob gunicorn (produção): evita que um
    # `import app` local (teste) marque execuções reais do servidor.
    if "gunicorn" in sys.modules:
        _z = db.limpar_runs_travadas()
        if _z:
            app.logger.info("Limpas %d execução(ões) travada(s) no startup.", _z)
except Exception as _e:
    app.logger.error("Falha ao inicializar banco: %s", _e)

# ── Job store (em memória) ────────────────────────────────────────────────────
_jobs: dict = {}
_jobs_lock = threading.Lock()


def _run_job(job_id: str, data: str, convenios: list, segmentos: list) -> None:
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    def progress(msg: str) -> None:
        with _jobs_lock:
            _jobs[job_id].setdefault("log", []).append(str(msg))

    try:
        zip_bytes, relatorio = processar_dia(data, convenios, segmentos, progress_cb=progress)
        with _jobs_lock:
            _jobs[job_id].update(
                {"status": "done", "zip_bytes": zip_bytes, "relatorio": relatorio}
            )
    except Exception as exc:
        tb = traceback.format_exc()
        app.logger.error("Erro no job %s:\n%s", job_id, tb)
        with _jobs_lock:
            _jobs[job_id].update({"status": "error", "error": str(exc), "traceback": tb})


def _run_ciclo_job(job_id: str, data: str, convenios: list, segmentos: list) -> None:
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    def progress(msg: str) -> None:
        with _jobs_lock:
            _jobs[job_id].setdefault("log", []).append(str(msg))

    try:
        relatorio = ciclo_dia(data, convenios, segmentos, progress_cb=progress)
        with _jobs_lock:
            _jobs[job_id].update({"status": "done", "relatorio": relatorio})
    except Exception as exc:
        tb = traceback.format_exc()
        app.logger.error("Erro no ciclo %s:\n%s", job_id, tb)
        with _jobs_lock:
            _jobs[job_id].update({"status": "error", "error": str(exc), "traceback": tb})


def _run_fechar_job(job_id: str, data: str, dry_run: bool, plano: str = "odontoprev") -> None:
    """Job do 'Fechar dia' (orquestrador completo fechar_dia.py)."""
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    def progress(msg: str) -> None:
        with _jobs_lock:
            _jobs[job_id].setdefault("log", []).append(str(msg))

    # Registra a execução no histórico (não bloqueia se o banco falhar).
    run_id = None
    try:
        run_id = db.criar_run(data, dry_run, plano=plano)
    except Exception as e:
        app.logger.error("Falha ao criar run no banco: %s", e)
    with _jobs_lock:
        _jobs[job_id]["run_id"] = run_id

    def _log_texto():
        with _jobs_lock:
            return "\n".join(_jobs[job_id].get("log", []))

    try:
        relatorio = fechar_dia(data, CONVENIOS, SEGMENTOS,
                               dry_run=dry_run, progress_cb=progress)
        if run_id is not None:
            try:
                db.finalizar_run_ok(run_id, relatorio, log_texto=_log_texto())
            except Exception as e:
                app.logger.error("Falha ao salvar run %s: %s", run_id, e)
        with _jobs_lock:
            _jobs[job_id].update({"status": "done", "relatorio": relatorio})
    except Exception as exc:
        tb = traceback.format_exc()
        app.logger.error("Erro no fechar_dia %s:\n%s", job_id, tb)
        if run_id is not None:
            try:
                db.finalizar_run_erro(run_id, str(exc) + "\n\n" + tb, log_texto=_log_texto())
            except Exception:
                pass
        with _jobs_lock:
            _jobs[job_id].update({"status": "error", "error": str(exc), "traceback": tb})


def _run_glosa_job(job_id: str, dia: str, contas: list, checar: bool,
                   checar_demo: bool = True) -> None:
    """Job da atualização do panorama de glosas (3 unidades por padrão)."""
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    def progress(msg: str) -> None:
        with _jobs_lock:
            _jobs[job_id].setdefault("log", []).append(str(msg))

    try:
        import time as _time
        from playwright.sync_api import sync_playwright
        from glosa_extrator import CONTAS, extrair_unidade
        lote = _time.strftime("%Y%m%d%H%M%S")
        alvo = [(u, l) for u, l in CONTAS if not contas or u in contas]
        total = 0
        with sync_playwright() as pw:
            for conta, label in alvo:
                progress(f"==== {label} ({conta}) — período até {dia} ====")
                r = extrair_unidade(pw, conta, label, dia, "_diag_glosa",
                                    checar_recursos=checar, checar_demonstrativo=checar_demo,
                                    log=progress)
                db.salvar_glosas(lote, dia, r["eventos"])
                total += len(r["eventos"])
                progress(f"[{label}] gravado ({len(r['eventos'])}).")
        try:
            rem = db.prune_glosa(lote, dia)
            if rem:
                progress(f"limpeza: {rem} evento(s) de lotes antigos do mês removidos.")
        except Exception as e:
            app.logger.error("prune glosa: %s", e)
        with _jobs_lock:
            _jobs[job_id].update({"status": "done", "lote": lote, "total": total})
    except Exception as exc:
        tb = traceback.format_exc()
        app.logger.error("Erro no glosa job %s:\n%s", job_id, tb)
        with _jobs_lock:
            _jobs[job_id].update({"status": "error", "error": str(exc), "traceback": tb})


def _run_anexacao_job(job_id: str, de: str, ate: str, contas: list, limite: int) -> None:
    """Job da varredura de anexação/faturamento (só-leitura, 3 unidades)."""
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    def progress(msg: str) -> None:
        with _jobs_lock:
            _jobs[job_id].setdefault("log", []).append(str(msg))

    try:
        import time as _time
        from playwright.sync_api import sync_playwright
        from anexacao_extrator import CONTAS, varrer_unidade
        lote = _time.strftime("%Y%m%d%H%M%S")
        alvo = [(u, l) for u, l in CONTAS if not contas or u in contas]
        total = 0
        with sync_playwright() as pw:
            for conta, label in alvo:
                progress(f"==== {label} ({conta}) — {de} a {ate} ====")
                r = varrer_unidade(pw, conta, label, de, ate, limite=limite, log=progress)
                db.salvar_anexacao(lote, de, ate, r["gtos"])
                total += len(r["gtos"])
                progress(f"[{label}] gravado ({len(r['gtos'])}).")
        # varredura cumulativa do mês -> remove snapshots antigos do mesmo período
        try:
            rem = db.prune_anexacao(lote, de)
            if rem:
                progress(f"limpeza: {rem} registro(s) de varreduras antigas do período removidos.")
        except Exception as e:
            app.logger.error("prune anexacao: %s", e)
        with _jobs_lock:
            _jobs[job_id].update({"status": "done", "lote": lote, "total": total})
    except Exception as exc:
        tb = traceback.format_exc()
        app.logger.error("Erro no anexacao job %s:\n%s", job_id, tb)
        with _jobs_lock:
            _jobs[job_id].update({"status": "error", "error": str(exc), "traceback": tb})


def _glosa_atualizou_hoje() -> bool:
    """True se já há um lote de glosa capturado hoje (horário de Brasília)."""
    try:
        lotes = db.glosa_lotes(1)
        if not lotes or not lotes[0].get("captured_at"):
            return False
        d = datetime.fromisoformat(lotes[0]["captured_at"])
        if _TZ:
            if d.tzinfo is None:
                from datetime import timezone as _tzc
                d = d.replace(tzinfo=_tzc.utc)
            d = d.astimezone(_TZ)
            hoje = datetime.now(_TZ).date()
        else:
            hoje = datetime.now().date()
        return d.date() == hoje
    except Exception:
        return False


def _glosa_scheduler():
    """Atualiza o panorama de glosas 1x/dia (após GLOSA_UPDATE_HOUR, Brasília).
    gunicorn roda 1 worker -> sem concorrência de agendadores."""
    try:
        hora = int(os.environ.get("GLOSA_UPDATE_HOUR", "6"))
    except ValueError:
        hora = 6
    while not _glosa_stop.is_set():
        try:
            # guarda periódico: execução presa em running há +3h = travada
            try:
                db.limpar_runs_travadas(horas=3)
            except Exception:
                pass
            agora = datetime.now(_TZ) if _TZ else datetime.now()
            if agora.hour >= hora and not _glosa_atualizou_hoje():
                dia = agora.strftime("%d/%m/%Y")
                jid = "auto" + uuid.uuid4().hex[:8]
                with _jobs_lock:
                    _jobs[jid] = {"status": "queued", "log": [], "kind": "glosa"}
                app.logger.info("Glosa auto-update iniciando (%s)…", dia)
                _run_glosa_job(jid, dia, [], True, True)
                app.logger.info("Glosa auto-update concluído.")
        except Exception as e:
            app.logger.error("Glosa scheduler: %s", e)
        _glosa_stop.wait(1800)  # re-checa a cada 30 min


_glosa_stop = threading.Event()
if os.environ.get("GLOSA_AUTO_UPDATE", "1") != "0":
    threading.Thread(target=_glosa_scheduler, daemon=True).start()


def _anexacao_atualizou_hoje() -> bool:
    try:
        lotes = db.anexacao_lotes(1)
        if not lotes or not lotes[0].get("captured_at"):
            return False
        d = datetime.fromisoformat(lotes[0]["captured_at"])
        if _TZ:
            if d.tzinfo is None:
                from datetime import timezone as _tzc
                d = d.replace(tzinfo=_tzc.utc)
            d = d.astimezone(_TZ)
            hoje = datetime.now(_TZ).date()
        else:
            hoje = datetime.now().date()
        return d.date() == hoje
    except Exception:
        return False


def _anexacao_scheduler():
    """Varre anexação/faturamento das 3 unidades 1x/dia (após ANEXACAO_UPDATE_HOUR,
    default 7h Brasília — escalonado da glosa p/ não rodarem juntas)."""
    try:
        hora = int(os.environ.get("ANEXACAO_UPDATE_HOUR", "7"))
    except ValueError:
        hora = 7
    while not _glosa_stop.is_set():
        try:
            agora = datetime.now(_TZ) if _TZ else datetime.now()
            if agora.hour >= hora and not _anexacao_atualizou_hoje():
                hoje = agora.strftime("%d/%m/%Y")
                de = "01/" + hoje[3:]
                jid = "anxauto" + uuid.uuid4().hex[:8]
                with _jobs_lock:
                    _jobs[jid] = {"status": "queued", "log": [], "kind": "anexacao"}
                app.logger.info("Anexação auto-update iniciando (%s a %s)…", de, hoje)
                _run_anexacao_job(jid, de, hoje, [], 0)
                app.logger.info("Anexação auto-update concluído.")
        except Exception as e:
            app.logger.error("Anexacao scheduler: %s", e)
        _glosa_stop.wait(1800)


if os.environ.get("ANEXACAO_AUTO_UPDATE", "1") != "0":
    threading.Thread(target=_anexacao_scheduler, daemon=True).start()


# ── Rotas ─────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    """Tela principal — Dashboard (com 'Executar Agora')."""
    return render_template("dashboard.html")


@app.route("/fechar-simples")
def fechar_simples():
    """Tela enxuta de 'Fechar o dia' (fallback)."""
    return render_template("fechar.html")


@app.route("/gtos")
def gtos_page():
    """Detalhe de GTOs / funil de um dia (mockup 2)."""
    return render_template("gtos.html")


@app.route("/relatorio")
def index():
    """Tela antiga (relatório analítico xlsx + download ZIP)."""
    return render_template("index.html", convenios=CONVENIOS, segmentos=SEGMENTOS)


# ── Relatório de execução (visual + PDF) ───────────────────────────────────────
# Cada status vira um grupo visual, com rótulo, cor e o "porquê" determinístico.
STATUS_META = {
    "ENVIADO":     {"label": "Anexado",            "cls": "ok",   "icon": "✓",
                    "desc": "Laudo e imagens anexados na GTO."},
    "PRONTO":      {"label": "Pronto para anexar", "cls": "ok",   "icon": "✓",
                    "desc": "Arquivos encontrados — seriam anexados numa execução real (simulação)."},
    "JA_ANEXADO":  {"label": "Já estava anexado",  "cls": "ok",   "icon": "✓",
                    "desc": "Os arquivos já estavam na GTO (nada a reenviar)."},
    "SEM_LAUDO":   {"label": "Sem laudo",          "cls": "warn", "icon": "!",
                    "desc": "Exame ainda não laudado no PRORADIS."},
    "SEM_IMAGENS": {"label": "Sem imagens",        "cls": "warn", "icon": "!",
                    "desc": "Sem imagens disponíveis no PRORADIS."},
    "SEM_MATCH":   {"label": "Não localizado",     "cls": "bad",  "icon": "✕",
                    "desc": "Paciente não localizado no PRORADIS."},
    "AMBIGUO":     {"label": "Ambíguo",            "cls": "bad",  "icon": "✕",
                    "desc": "Mais de um paciente com o mesmo nome — precisa conferência."},
    "ERRO_UPLOAD": {"label": "Erro ao anexar",     "cls": "bad",  "icon": "✕",
                    "desc": "Falha no envio do anexo."},
}
_ORDEM_STATUS = ["ENVIADO", "PRONTO", "JA_ANEXADO", "SEM_LAUDO", "SEM_IMAGENS",
                 "SEM_MATCH", "AMBIGUO", "ERRO_UPLOAD"]


def _fmt_run_datas(run: dict) -> dict:
    """Adiciona início/fim formatados (Brasília) e duração ao dict da run."""
    def _parse(s):
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s)
            return dt.astimezone(_TZ) if _TZ else dt
        except Exception:
            return None
    ini, fim = _parse(run.get("started_at")), _parse(run.get("finished_at"))
    run["ini_fmt"] = ini.strftime("%d/%m/%Y %H:%M") if ini else "—"
    run["fim_fmt"] = fim.strftime("%d/%m/%Y %H:%M") if fim else "—"
    if ini and fim:
        seg = max(int((fim - ini).total_seconds()), 0)
        m, s = divmod(seg, 60)
        run["dur_fmt"] = (f"{m}m {s}s" if m else f"{s}s")
    else:
        run["dur_fmt"] = "—"
    return run


def _agrupar_run(run: dict) -> list:
    """Agrupa os itens por status (na ordem de _ORDEM_STATUS), pulando vazios."""
    itens = run.get("itens", []) or []
    por_status = {}
    for it in itens:
        st = (it.get("status") or "?").upper()
        por_status.setdefault(st, []).append(it)
    grupos = []
    vistos = set()
    for st in _ORDEM_STATUS + sorted(por_status.keys()):
        if st in vistos or st not in por_status:
            continue
        vistos.add(st)
        meta = STATUS_META.get(st, {"label": st.title(), "cls": "warn", "icon": "•", "desc": ""})
        grupos.append({"status": st, "meta": meta, "itens": por_status[st]})
    return grupos


@app.route("/relatorio/run/<int:run_id>")
def relatorio_run(run_id: int):
    """Relatório visual de uma execução (o que foi feito, o que não, e por quê)."""
    run = db.run_detalhe(run_id)
    if not run:
        return ("Execução não encontrada.", 404)
    _fmt_run_datas(run)
    embed = request.args.get("embed") in ("1", "true", "yes")
    return render_template("relatorio_run.html", run=run,
                           grupos=_agrupar_run(run), pdf=False, embed=embed)


@app.route("/relatorio/run/<int:run_id>.pdf")
def relatorio_run_pdf(run_id: int):
    """Mesmo relatório, renderizado em PDF pelo Chromium (Playwright). Download 1-clique."""
    run = db.run_detalhe(run_id)
    if not run:
        return ("Execução não encontrada.", 404)
    _fmt_run_datas(run)
    html = render_template("relatorio_run.html", run=run,
                           grupos=_agrupar_run(run), pdf=True)
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = br.new_page()
            pg.set_content(html, wait_until="networkidle")
            pdf_bytes = pg.pdf(format="A4", print_background=True,
                               margin={"top": "12mm", "bottom": "12mm",
                                       "left": "10mm", "right": "10mm"})
            br.close()
    except Exception as exc:
        app.logger.error("Falha ao gerar PDF da run %s: %s", run_id, exc)
        return (f"Falha ao gerar PDF: {exc}", 500)
    nome = f"relatorio_{(run.get('dia') or '').replace('/', '-')}_run{run_id}.pdf"
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name=nome)


# ── Relatório de execuções (pipeline novo) — dentro de /relatorios ────────────
def _fmt_quando(dt):
    """Datetime UTC -> 'DD/MM/AAAA HH:MM' no horário de Brasília (UTC-3)."""
    if not dt:
        return ""
    try:
        from datetime import timedelta
        return (dt - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(dt)


@app.route("/relatorios/execucoes")
def relatorios_execucoes():
    execs = db.listar_execucoes(300)
    for e in execs:
        e["quando"] = _fmt_quando(e.get("criado_em"))
    tot = {
        "n": len(execs),
        "faturadas": sum(e["faturadas"] for e in execs),
        "nao_faturadas": sum(e["nao_faturadas"] for e in execs),
        "tempo_total": sum(e["tempo_total"] for e in execs),
        "tempo_medio": round(sum(e["tempo_total"] for e in execs) / len(execs)) if execs else 0,
    }
    return render_template("execucoes.html", execs=execs, tot=tot)


@app.route("/relatorios/execucao/<int:eid>")
def relatorios_execucao(eid: int):
    ex = db.get_execucao(eid)
    if not ex:
        return ("Execução não encontrada.", 404)
    ex["quando"] = _fmt_quando(ex.get("criado_em"))
    return render_template("execucao.html", ex=ex, pdf=False)


@app.route("/relatorios/execucao/<int:eid>.pdf")
def relatorios_execucao_pdf(eid: int):
    ex = db.get_execucao(eid)
    if not ex:
        return ("Execução não encontrada.", 404)
    ex["quando"] = _fmt_quando(ex.get("criado_em"))
    html = render_template("execucao.html", ex=ex, pdf=True)
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = br.new_page()
            pg.set_content(html, wait_until="networkidle")
            pdf_bytes = pg.pdf(format="A4", print_background=True,
                               margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
            br.close()
    except Exception as exc:
        return (f"Falha ao gerar PDF: {exc}", 500)
    nome = f"execucao_{(ex.get('dia') or '').replace('/', '-')}_{eid}.pdf"
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name=nome)


@app.route("/relatorios/execucao/<int:eid>.xlsx")
def relatorios_execucao_xlsx(eid: int):
    ex = db.get_execucao(eid)
    if not ex:
        return ("Execução não encontrada.", 404)
    import pandas as pd

    def _anexado(it):
        base = "laudo + imagens"
        return base + (f" + solicitação ({it['solicitacao']})" if it.get("solicitacao") else "")
    df_f = pd.DataFrame([{"GTO": i["gto"], "Paciente": i["paciente"],
                          "Exames (GTO)": i.get("exames_gto") or "",
                          "O que foi anexado": _anexado(i)}
                         for i in ex["faturadas_itens"]])
    df_n = pd.DataFrame([{"GTO": i["gto"], "Paciente": i["paciente"],
                          "Exames (GTO)": i.get("exames_gto") or "",
                          "Motivo (não faturada)": i.get("motivo") or ""}
                         for i in ex["nao_faturadas_itens"]])
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    # colunas largas (com quebra de texto) — pra não ficar "embolado"
    LARGAS = {"O que foi anexado", "Motivo (não faturada)"}

    def _estilizar(ws, df):
        head_fill = PatternFill("solid", fgColor="0F7A4F")
        head_font = Font(bold=True, color="FFFFFF", size=11)
        borda = Border(bottom=Side(style="thin", color="DDE5E0"))
        ws.row_dimensions[1].height = 26
        for ci, col in enumerate(df.columns, start=1):
            cell = ws.cell(row=1, column=ci)
            cell.fill = head_fill
            cell.font = head_font
            cell.alignment = Alignment(vertical="center",
                                       horizontal="left", wrap_text=False)
            # largura: maior conteúdo da coluna, com tetos por tipo
            larga = col in LARGAS
            vals = [str(col)] + [str(v) for v in df[col].tolist() if v is not None]
            maxlen = max((len(v) for v in vals), default=10)
            ws.column_dimensions[cell.column_letter].width = min(maxlen + 3, 70 if larga else 32)
        # corpo: quebra de texto nas colunas largas + borda leve + alinhamento topo
        for ri in range(2, ws.max_row + 1):
            for ci, col in enumerate(df.columns, start=1):
                c = ws.cell(row=ri, column=ci)
                c.alignment = Alignment(vertical="top", wrap_text=(col in LARGAS))
                c.border = borda
                c.font = Font(size=10)
        ws.freeze_panes = "A2"          # cabeçalho fixo ao rolar

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        fat = df_f if not df_f.empty else pd.DataFrame([{"(sem faturadas)": "—"}])
        nao = df_n if not df_n.empty else pd.DataFrame([{"(sem não faturadas)": "—"}])
        fat.to_excel(w, sheet_name="Faturadas", index=False)
        nao.to_excel(w, sheet_name="Não faturadas", index=False)
        _estilizar(w.sheets["Faturadas"], fat)
        _estilizar(w.sheets["Não faturadas"], nao)
    buf.seek(0)
    nome = f"execucao_{(ex.get('dia') or '').replace('/', '-')}_{eid}.xlsx"
    return send_file(buf, as_attachment=True, download_name=nome,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/relatorios/execucao/<int:eid>.json")
def relatorios_execucao_json(eid: int):
    ex = db.get_execucao(eid)
    if not ex:
        return jsonify({"error": "não encontrada"}), 404
    ex.pop("criado_em", None)  # datetime não serializa
    return jsonify(ex)


# ── Faturar dia (UI do pipeline novo) ─────────────────────────────────────────
@app.route("/faturar")
def faturar_page():
    planos = [{"codigo": c, "label": p["label"]} for c, p in PLANOS.items()]
    return render_template("faturar.html", planos=planos, planos_inativos=PLANOS_INATIVOS)


@app.route("/faturar/run")
def faturar_run():
    import time as _time
    data = request.args.get("data")
    if not data:
        return jsonify({"error": "informe a data"}), 400
    plano = request.args.get("plano") or ""
    if plano and plano not in PLANOS:
        return jsonify({"error": "plano inválido"}), 400
    dry = request.args.get("dry", "0") != "0"
    jid = uuid.uuid4().hex[:8]
    review_dir = f"/tmp/esteira_rev/{jid}"
    job = {"log": [], "done": False, "resumo": None, "error": None,
           "review_dir": review_dir, "execucao_id": None, "t0": _time.monotonic(),
           "dia": data}
    _esteira_jobs[jid] = job
    gkey = os.environ.get("GEMINI_API_KEY")

    def _go():
        try:
            from esteira import rodar_esteira
            job["resumo"] = rodar_esteira(data, 6, 3, 5, lambda m: job["log"].append(m),
                                          gemini_key=gkey, review_dir=review_dir,
                                          k_attach=3, dry_run=dry, conta=(plano or None))
            if not dry and job["resumo"]:
                try:
                    job["execucao_id"] = db.salvar_execucao(job["resumo"])
                except Exception as e:
                    job["log"].append(f"(falha ao salvar: {str(e)[:80]})")
        except Exception as e:
            job["error"] = str(e)
            job["log"].append(f"ERRO: {str(e)[:140]}")
        finally:
            job["done"] = True

    threading.Thread(target=_go, daemon=True).start()
    return jsonify({"job": jid})


def _esteira_progress(job: dict) -> dict:
    """Deriva progresso amigável (%, mensagem, ETA em seg) a partir do log do job."""
    import time as _time
    import re as _re
    log = job.get("log", [])
    pend = sum(1 for l in log if ">>> PENDENTE" in l)
    baix = sum(1 for l in log if "-> BAIXADO" in l)
    anex = sum(1 for l in log if "[ANEX" in l)
    # tempos (s desde o início) das DECISÕES concluídas — base estável p/ ETA
    dec_t = []
    for l in log:
        if "[DEC" in l and "mem=" in l:
            m = _re.match(r"\[\s*(\d+)s\]", l.strip())
            if m:
                dec_t.append(int(m.group(1)))
    dec = len(dec_t)
    done = bool(job.get("done"))
    t0 = job.get("t0")
    elapsed = (_time.monotonic() - t0) if t0 else 0
    total = pend
    # Médias medidas em runs reais: setup/descoberta ~55s, ~9s por GTO.
    SETUP_S, POR_GTO_S, BASELINE_GENERICO = 55, 9, 190
    est_total = (SETUP_S + POR_GTO_S * total) if total > 0 else BASELINE_GENERICO
    # Barra: acompanha o TEMPO (piso que sobe sempre) e pula à frente quando o
    # conteúdo real (downloads/decisões) avança mais rápido — nunca fica travada.
    if done:
        pct = 100
    else:
        time_pct = min(95, int(elapsed / max(est_total, 30) * 100))
        content_pct = int((baix + dec) / (2 * total) * 100) if total > 0 else 0
        pct = min(97, max(time_pct, content_pct))
    # ETA — sempre dá um número (sem "estimando"); fica mais preciso ao longo:
    #  1) com >=2 decisões: taxa REAL de decisões (a etapa lenta);
    #  2) descoberta feita (sabe o nº): baseline SETUP + nº*POR_GTO;
    #  3) setup/descoberta ainda rolando: baseline fixo genérico.
    eta = None
    if not done:
        if total > 0 and dec >= total:
            eta = 4  # tudo decidido, anexando/finalizando
        elif dec >= 2:
            rate = (dec_t[-1] - dec_t[0]) / (dec - 1)        # seg por decisão
            desde = max(0, elapsed - dec_t[-1])               # já passou desde a última
            eta = max(3, int((total - dec) * rate - desde))
        elif total > 0:                                       # baixando, sem decisão ainda
            eta = max(5, int(SETUP_S + POR_GTO_S * total - elapsed))
        else:                                                 # setup/descoberta
            eta = max(8, int(BASELINE_GENERICO - elapsed))
    if done:
        msg = "Concluído!"
    elif total == 0:
        msg = "Procurando GTOs pendentes do dia…"
    elif anex > 0:
        msg = f"Anexando no RedeUna… ({anex} enviado{'s' if anex != 1 else ''})"
    elif dec > 0:
        msg = f"Analisando solicitações com IA… ({dec} de {total})"
    elif baix > 0:
        msg = f"Baixando exames e laudos… ({baix} de {total})"
    else:
        msg = f"{total} pendentes encontrados — preparando…"
    return {"pct": pct, "msg": msg, "eta": eta, "total": total,
            "baixados": baix, "decididos": dec, "anexados": anex, "elapsed": int(elapsed)}


@app.route("/faturar/status/<jid>")
def faturar_status(jid: str):
    job = _esteira_jobs.get(jid)
    if not job:
        return jsonify({"error": "job não encontrado"}), 404
    r = job.get("resumo") or {}
    return jsonify({
        "done": job["done"], "error": job["error"],
        "execucao_id": job.get("execucao_id"),
        "prog": _esteira_progress(job),
        "resumo": {k: r.get(k) for k in ("solic_auto", "justificativa", "revisao",
                   "anexado_ok", "tempo_total", "pendentes")} if r else None,
    })


@app.route("/faturar/log/<jid>")
def faturar_log(jid: str):
    from flask import Response
    job = _esteira_jobs.get(jid)
    if not job:
        return "job não encontrado", 404
    head = f"# Log técnico — execução {jid} (dia {job.get('dia')})\n\n"
    return Response(head + "\n".join(job.get("log", [])),
                    mimetype="text/plain; charset=utf-8")


# ── APIs do Dashboard ─────────────────────────────────────────────────────────

@app.route("/api/dashboard")
def api_dashboard():
    """Dados agregados p/ o dashboard: última execução, semana, fila, totais."""
    try:
        with _jobs_lock:
            processando = sum(1 for j in _jobs.values()
                              if j.get("status") in ("running", "queued"))
            rodando_plano = {j.get("plano") for j in _jobs.values()
                             if j.get("status") in ("running", "queued")}
        ultima = db.run_mais_recente()
        # monta a lista de planos do registro + status (última execução de cada)
        por_plano = db.status_por_plano()
        lista_planos = []
        for p in planos_mod.listar_planos():
            lista_planos.append({
                "slug": p["slug"], "nome": p["nome"], "ativo": p.get("ativo", False),
                "rodando": p["slug"] in rodando_plano,
                "ultima": por_plano.get(p["slug"]),
            })
        return jsonify({
            "ultima": ultima,
            "planos": lista_planos,
            "recentes": db.ultimas_runs(8),
            "semana": db.serie_semana(),
            "revisao": db.fila_revisao(30),
            "totais": db.totais_gerais(),
            "processando": processando,
        })
    except Exception as exc:
        app.logger.error("Erro em /api/dashboard: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/gtos")
def api_gtos():
    """Funil + lista de GTOs de um dia (DD/MM/AAAA) ou da execução mais recente."""
    dia = request.args.get("dia", "").strip() or None
    try:
        run = db.run_mais_recente(dia)
        return jsonify({"run": run})
    except Exception as exc:
        app.logger.error("Erro em /api/gtos: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/planos-periodo")
def api_planos_periodo():
    """Gráfico empilhado: por plano, desfecho das GTOs no período [de, ate].
    `de`/`ate` em YYYY-MM-DD (o cliente calcula a partir de Hoje/Semana/Mês)."""
    de = request.args.get("de", "").strip()
    ate = request.args.get("ate", "").strip()
    try:
        agg = db.gtos_por_plano_periodo(de, ate) if de and ate else {}
        with _jobs_lock:
            rodando = {j.get("plano") for j in _jobs.values()
                       if j.get("status") in ("running", "queued")}
        planos = []
        for p in planos_mod.listar_planos():
            c = agg.get(p["slug"], {})
            planos.append({
                "slug": p["slug"], "nome": p["nome"], "ativo": p.get("ativo", False),
                "rodando": p["slug"] in rodando,
                "anexadas": c.get("anexadas", 0), "sem_laudo": c.get("sem_laudo", 0),
                "erros": c.get("erros", 0), "simulacao": c.get("simulacao", 0),
                "revisao": c.get("revisao", 0), "total": c.get("total", 0),
                "dias": c.get("dias", 0),
            })
        return jsonify({"de": de, "ate": ate, "planos": planos})
    except Exception as exc:
        app.logger.error("Erro em /api/planos-periodo: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/plano/<slug>")
def plano_detalhe_page(slug: str):
    """Tela de detalhe: GTOs processadas de um plano no período."""
    return render_template("plano_detalhe.html",
                           slug=slug, nome=planos_mod.nome_plano(slug),
                           de=request.args.get("de", ""), ate=request.args.get("ate", ""))


@app.route("/api/plano-detalhe")
def api_plano_detalhe():
    plano = request.args.get("plano", "").strip()
    de = request.args.get("de", "").strip()
    ate = request.args.get("ate", "").strip()
    if not plano or not de or not ate:
        return jsonify({"error": "Informe plano, de e ate."}), 400
    try:
        d = db.itens_plano_periodo(plano, de, ate)
        d["nome"] = planos_mod.nome_plano(plano)
        return jsonify(d)
    except Exception as exc:
        app.logger.error("Erro em /api/plano-detalhe: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Panorama de Glosas / Recurso ──────────────────────────────────────────────

SITUACAO_META = {
    "A_RECORRER":           {"label": "A recorrer",  "cls": "warn",
                             "desc": "Glosada e ainda com recurso disponível na guia."},
    "RECURSO_OU_RESOLVIDA": {"label": "Recurso enviado / em análise", "cls": "info",
                             "desc": "Recursável, mas a guia já não mostra eventos glosados "
                                     "(recurso provavelmente já enviado ou resolvido)."},
    "RECURSO_REJEITADO":    {"label": "Recurso recusado (refazer)", "cls": "rej",
                             "desc": "Reanálise (recurso) feita de forma incorreta — já passou "
                                     "pelo recurso e foi recusada; precisa refazer."},
    "RESOLVIDA":            {"label": "Resolvida (paga)", "cls": "ok",
                             "desc": "Demonstrativo mostra a guia paga e sem glosa "
                                     "(recurso deferido ou glosa revertida)."},
    "GLOSA_CONFIRMADA":     {"label": "Glosa confirmada", "cls": "bad",
                             "desc": "Demonstrativo confirma a glosa no pagamento "
                                     "(recurso indeferido ou não recorrido)."},
    "NAO_RECURSAVEL":       {"label": "Não recursável", "cls": "bad",
                             "desc": "Recuperação de valores ou sem opção de recurso na guia."},
    "GLOSADA":              {"label": "Glosada", "cls": "neutral",
                             "desc": "Glosada (estado de recurso ainda não verificado)."},
}


def _glosa_view(lote: str = None) -> dict:
    pan = db.glosa_panorama(lote)
    evs = db.glosa_eventos(lote)
    pan["eventos"] = evs
    pan["meta"] = SITUACAO_META
    pan["lotes"] = db.glosa_lotes(12)
    return pan


@app.route("/glosas")
def glosas_page():
    """Panorama de glosas e recurso das unidades (tela, com exportar)."""
    return render_template("glosas.html")


@app.route("/api/glosas")
def api_glosas():
    try:
        return jsonify(_glosa_view(request.args.get("lote") or None))
    except Exception as exc:
        app.logger.error("Erro em /api/glosas: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/glosas/relatorio")
def glosas_relatorio():
    """Versão imprimível do panorama (usada também para gerar o PDF)."""
    pan = _glosa_view(request.args.get("lote") or None)
    embed = request.args.get("embed") in ("1", "true", "yes")
    return render_template("glosas_relatorio.html", pan=pan, pdf=False, embed=embed)


@app.route("/glosas.pdf")
def glosas_pdf():
    pan = _glosa_view(request.args.get("lote") or None)
    html = render_template("glosas_relatorio.html", pan=pan, pdf=True, embed=False)
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = br.new_page()
            pg.set_content(html, wait_until="networkidle")
            pdf_bytes = pg.pdf(format="A4", print_background=True,
                               margin={"top": "12mm", "bottom": "12mm",
                                       "left": "10mm", "right": "10mm"})
            br.close()
    except Exception as exc:
        app.logger.error("Falha ao gerar PDF de glosas: %s", exc)
        return (f"Falha ao gerar PDF: {exc}", 500)
    nome = f"glosas_{(pan.get('dia') or '').replace('/', '-')}.pdf"
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name=nome)


@app.route("/glosas.xlsx")
def glosas_xlsx():
    import pandas as pd
    pan = _glosa_view(request.args.get("lote") or None)
    evs = pan.get("eventos", [])
    meta = SITUACAO_META
    # Resumo por unidade x situação
    resumo = []
    for u in pan.get("por_unidade", []):
        linha = {"Unidade": u["unidade"], "Total": u["total"]}
        for k, lbl in db.GLOSA_SITUACOES:
            linha[lbl] = u.get(k, 0)
        resumo.append(linha)
    det = [{
        "Unidade": e["unidade"], "Guia": e["ficha"], "Paciente": e["paciente"],
        "Procedimento": e["evento"], "Cód. glosa": e["glosa_cod"],
        "Motivo": e["glosa_motivo"],
        "Situação": meta.get(e["situacao"], {}).get("label", e["situacao"]),
    } for e in evs]
    motivos = [{"Cód.": m["glosa_cod"], "Motivo": m["glosa_motivo"], "Qtd": m["total"]}
               for m in pan.get("por_motivo", [])]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        (pd.DataFrame(resumo) if resumo else pd.DataFrame([{"Unidade": "—"}])).to_excel(
            xw, sheet_name="Resumo", index=False)
        (pd.DataFrame(motivos) if motivos else pd.DataFrame([{"Motivo": "—"}])).to_excel(
            xw, sheet_name="Por motivo", index=False)
        (pd.DataFrame(det) if det else pd.DataFrame([{"Guia": "—"}])).to_excel(
            xw, sheet_name="Glosas", index=False)
    buf.seek(0)
    nome = f"glosas_{(pan.get('dia') or '').replace('/', '-')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=nome,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/glosas/atualizar", methods=["POST"])
def glosas_atualizar():
    """Dispara a atualização do panorama (background)."""
    body = request.get_json(silent=True) or {}
    dia = (body.get("dia") or datetime.now().strftime("%d/%m/%Y")).strip()
    checar = bool(body.get("checar_recurso", True))
    checar_demo = bool(body.get("checar_demonstrativo", True))
    contas = body.get("contas") or []
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "log": [], "kind": "glosa"}
    threading.Thread(target=_run_glosa_job, args=(job_id, dia, contas, checar, checar_demo),
                     daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/glosas/atualizar/status/<job_id>")
def glosas_atualizar_status(job_id: str):
    with _jobs_lock:
        j = _jobs.get(job_id)
        if not j:
            return jsonify({"error": "job não encontrado"}), 404
        return jsonify({"status": j.get("status"), "log": j.get("log", [])[-40:],
                        "total": j.get("total"), "lote": j.get("lote"),
                        "error": j.get("error")})


# ── Relatórios (hub) ──────────────────────────────────────────────────────────

def _relatorios_data() -> dict:
    """Junta faturamento (varredura) + glosas num pacote único p/ os relatórios."""
    anx = db.anexacao_panorama()
    glo = db.glosa_panorama()
    cat = anx.get("por_categoria", {}) or {}
    gsit = glo.get("glosado_situacao", {}) or {}
    sit = glo.get("por_situacao", {}) or {}
    # por unidade: cruza faturamento (anx) com glosado (glo)
    glo_uni = {u["unidade"]: u for u in glo.get("por_unidade", [])}
    por_unidade = []
    for u in anx.get("por_unidade", []):
        gu = glo_uni.get(u["unidade"], {})
        por_unidade.append({
            "unidade": u["unidade"], "total": u["total"],
            "faturada": u.get("FATURADA", 0), "a_faturar": u.get("A_FATURAR", 0),
            "sem_anexo": u.get("SEM_ANEXO", 0), "liberada": u.get("LIBERADA", 0),
            "cancelada": u.get("CANCELADA", 0),
            "glosas": gu.get("total", 0), "glosado": round(gu.get("glosado", 0), 2),
        })
    return {
        "anx": anx, "glo": glo,
        "resumo": {
            "total_gtos": anx.get("total", 0),
            "faturadas": cat.get("FATURADA", 0), "a_faturar": cat.get("A_FATURAR", 0),
            "sem_anexo": cat.get("SEM_ANEXO", 0), "liberadas": cat.get("LIBERADA", 0),
            "canceladas": cat.get("CANCELADA", 0),
            "glosas": glo.get("total", 0),
            "glosado_total": glo.get("total_glosado", 0),
            "recuperavel": round(gsit.get("A_RECORRER", 0), 2),
            "perda_confirmada": round(gsit.get("GLOSA_CONFIRMADA", 0), 2),
            "a_recorrer_n": sit.get("A_RECORRER", 0),
            "glosa_confirmada_n": sit.get("GLOSA_CONFIRMADA", 0),
        },
        "por_unidade": por_unidade,
        "pendencias": {
            "a_faturar": db.anexacao_gtos(categoria="A_FATURAR"),
            "a_recorrer": [e for e in db.glosa_eventos() if e["situacao"] == "A_RECORRER"],
        },
        "motivos_glosa": glo.get("por_motivo", []),
        "meta_sit": SITUACAO_META, "meta_cat": CATEGORIA_META,
    }


@app.route("/relatorios")
def relatorios_page():
    return render_template("relatorios.html")


@app.route("/api/relatorios")
def api_relatorios():
    try:
        return jsonify(_relatorios_data())
    except Exception as exc:
        app.logger.error("Erro em /api/relatorios: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/relatorios.pdf")
def relatorios_pdf():
    data = _relatorios_data()
    html = render_template("relatorios_pdf.html", d=data)
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = br.new_page()
            pg.set_content(html, wait_until="networkidle")
            pdf_bytes = pg.pdf(format="A4", print_background=True,
                               margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
            br.close()
    except Exception as exc:
        return (f"Falha ao gerar PDF: {exc}", 500)
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name="relatorios_radiobras.pdf")


# ── Anexação / Faturamento (varredura só-leitura) ─────────────────────────────

CATEGORIA_META = {
    "FATURADA":  {"label": "Faturada", "cls": "ok",
                  "desc": "2+ anexos — laudo e entrega já anexados"},
    "A_FATURAR": {"label": "A faturar", "cls": "warn",
                  "desc": "Só 1 anexo — falta o 2º para faturar"},
    "SEM_ANEXO": {"label": "Sem anexo", "cls": "bad",
                  "desc": "Nenhum anexo enviado ainda"},
    "LIBERADA":  {"label": "Liberada p/ assinatura", "cls": "info",
                  "desc": "Senha liberada no portal — aguardando"},
    "CANCELADA": {"label": "Cancelada", "cls": "neutral",
                  "desc": "GTO cancelada ou não autorizada"},
    "ERRO":      {"label": "Erro de leitura", "cls": "bad",
                  "desc": "Não foi possível ler a GTO"},
}


def _anexacao_view(lote: str = None) -> dict:
    pan = db.anexacao_panorama(lote)
    pan["gtos"] = db.anexacao_gtos(lote)
    pan["meta"] = CATEGORIA_META
    pan["lotes"] = db.anexacao_lotes(12)
    return pan


@app.route("/api/anexacao")
def api_anexacao():
    try:
        return jsonify(_anexacao_view(request.args.get("lote") or None))
    except Exception as exc:
        app.logger.error("Erro em /api/anexacao: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/anexacao/atualizar", methods=["POST"])
def anexacao_atualizar():
    body = request.get_json(silent=True) or {}
    hoje = datetime.now().strftime("%d/%m/%Y")
    de = (body.get("de") or ("01/" + hoje[3:])).strip()
    ate = (body.get("ate") or hoje).strip()
    contas = body.get("contas") or []
    limite = int(body.get("limite") or 0)
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "log": [], "kind": "anexacao"}
    threading.Thread(target=_run_anexacao_job, args=(job_id, de, ate, contas, limite),
                     daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/anexacao/atualizar/status/<job_id>")
def anexacao_atualizar_status(job_id: str):
    with _jobs_lock:
        j = _jobs.get(job_id)
        if not j:
            return jsonify({"error": "job não encontrado"}), 404
        return jsonify({"status": j.get("status"), "log": j.get("log", [])[-40:],
                        "total": j.get("total"), "lote": j.get("lote"),
                        "error": j.get("error")})


@app.route("/api/diag")
def api_diag():
    """Diagnóstico de saúde — para inspeção remota sem acesso aos logs do servidor.
    Reporta: banco conectado, presença de credenciais e últimas execuções (c/ erro)."""
    from sqlalchemy import text
    diag = {"app": "ok"}
    # banco
    try:
        with db.engine.connect() as c:
            c.execute(text("SELECT 1"))
        diag["db_ok"] = True
        diag["db_host"] = db.DATABASE_URL.split("@")[-1]
    except Exception as e:
        diag["db_ok"] = False
        diag["db_error"] = str(e)[:200]
    # credenciais presentes? (não expõe valores)
    diag["cred"] = {
        "smartris": bool(os.environ.get("SMARTRIS_EMAIL") and os.environ.get("SMARTRIS_PASSWORD")),
        "odontoprev": bool(os.environ.get("ODONTOPREV_USER") and os.environ.get("ODONTOPREV_PASSWORD")),
    }
    # jobs em memória + últimas execuções (com erro resumido)
    with _jobs_lock:
        diag["jobs_ativos"] = sum(1 for j in _jobs.values()
                                  if j.get("status") in ("running", "queued"))
    try:
        diag["runs"] = [{
            "id": r["id"], "plano": r["plano"], "dia": r["dia"], "status": r["status"],
            "enviados": r["enviados"], "erros": r["erros"],
            "erro_msg": (r.get("erro_msg") or "")[:300],
            "finished_at": r["finished_at"],
        } for r in db.runs_recentes(10)]
    except Exception as e:
        diag["runs_error"] = str(e)[:200]
    return jsonify(diag)


@app.route("/fechar", methods=["POST"])
def fechar_route():
    """Inicia o fechamento do dia (download + anexo no OdontoPrev). Assíncrono."""
    data = request.form.get("data", "").strip()
    plano = (request.form.get("plano", "") or "odontoprev").strip()
    # 'simular' = dry-run (não anexa, só mostra o que faria)
    dry_run = request.form.get("simular", "").lower() in ("1", "true", "on", "yes")
    if not data:
        return jsonify({"error": "Informe o dia (DD/MM/AAAA)."}), 400
    if not re.match(r"^\d{2}/\d{2}/\d{4}$", data):
        return jsonify({"error": "Data inválida. Use DD/MM/AAAA."}), 400
    if not planos_mod.plano_ativo(plano):
        return jsonify({"error": f"O plano '{planos_mod.nome_plano(plano)}' ainda não "
                                 "está configurado para automação."}), 400

    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "log": [], "plano": plano}
    threading.Thread(
        target=_run_fechar_job, args=(job_id, data, dry_run, plano), daemon=True
    ).start()
    return jsonify({"job_id": job_id, "dry_run": dry_run})


@app.route("/fechar/status/<job_id>")
def fechar_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job não encontrado."}), 404
    resp: dict = {"status": job["status"], "log": job.get("log", [])}
    resp["run_id"] = job.get("run_id")
    if job["status"] == "error":
        # NÃO vaza o traceback/erro técnico pro usuário — só uma mensagem amigável
        # + um código (run_id) p/ a equipe achar os detalhes nos logs (DB/diag).
        resp["error"] = "Não foi possível concluir o processamento."
        resp["error_code"] = str(job.get("run_id") or job_id)
    if job["status"] == "done":
        rel = job.get("relatorio", {})
        resp["resumo"] = rel.get("resumo", {})
        resp["itens"] = rel.get("itens", [])
        resp["dry_run"] = rel.get("dry_run", False)
    return jsonify(resp)


@app.route("/gerar", methods=["POST"])
def gerar():
    date_from = request.form.get("date_from", "").strip()
    date_to = request.form.get("date_to", "").strip()
    selected_convenios = request.form.getlist("convenios")
    selected_segmentos = request.form.getlist("segmentos")

    if not date_from or not date_to:
        return jsonify({"error": "Informe o período."}), 400
    if not selected_convenios:
        return jsonify({"error": "Selecione ao menos um convênio."}), 400
    if not selected_segmentos:
        return jsonify({"error": "Selecione ao menos um segmento."}), 400

    try:
        email, password = get_credentials()
        convenio_map, segmento_map, cookies = discover_tokens_and_cookies(email, password)
        insurance_tokens = resolve_tokens(selected_convenios, convenio_map, "convenio")
        segment_tokens = resolve_tokens(selected_segmentos, segmento_map, "segmento")

        if not insurance_tokens:
            return jsonify({"error": "Nenhum convênio resolvido. Verifique os nomes."}), 400

        html = post_relatorio(cookies, insurance_tokens, segment_tokens, date_from, date_to)
        df, valor_total, num_exames = parse_html_to_df(html)

        if df.empty:
            return jsonify({"warning": "Nenhum exame encontrado para o período."}), 200

        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        buf.seek(0)
        date_tag = date_from.replace("/", "") + "_" + date_to.replace("/", "")
        return send_file(
            buf,
            as_attachment=True,
            download_name=f"pacientes_analitico_REDEUNNA_{date_tag}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as exc:
        tb = traceback.format_exc()
        app.logger.error("Erro em /gerar:\n%s", tb)
        return jsonify({"error": str(exc), "traceback": tb}), 500


@app.route("/baixar_dia", methods=["POST"])
def baixar_dia():
    date_from = request.form.get("date_from", "").strip()
    date_to = request.form.get("date_to", "").strip()
    selected_convenios = request.form.getlist("convenios")
    selected_segmentos = request.form.getlist("segmentos")

    if not date_from or not date_to:
        return jsonify({"error": "Informe o período."}), 400
    if not selected_convenios:
        return jsonify({"error": "Selecione ao menos um convênio."}), 400

    # Usar date_from como data do dia (dia único)
    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "log": []}

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, date_from, selected_convenios, selected_segmentos),
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/baixar_dia/status/<job_id>")
def baixar_dia_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job não encontrado."}), 404

    resp: dict = {"status": job["status"], "log": job.get("log", [])}
    if job["status"] == "error":
        resp["error"] = job.get("error", "")
    if job["status"] == "done":
        resp["resumo"] = job.get("relatorio", {}).get("resumo", {})
    return jsonify(resp)


@app.route("/baixar_dia/resultado/<job_id>")
def baixar_dia_resultado(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job não encontrado."}), 404
    if job["status"] != "done":
        return jsonify({"error": "Job ainda não concluído."}), 400

    zip_bytes = job["zip_bytes"]
    data_tag = (
        job.get("relatorio", {}).get("periodo", {}).get("de", "").replace("/", "")
    )
    filename = f"arquivos_REDEUNNA_{data_tag}.zip"
    buf = io.BytesIO(zip_bytes)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/zip")


@app.route("/ciclo_dia", methods=["POST"])
def ciclo_dia_route():
    date_from = request.form.get("date_from", "").strip()
    selected_convenios = request.form.getlist("convenios") or CONVENIOS
    selected_segmentos = request.form.getlist("segmentos") or SEGMENTOS

    if not date_from:
        return jsonify({"error": "Informe o dia."}), 400

    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "log": []}

    threading.Thread(
        target=_run_ciclo_job,
        args=(job_id, date_from, selected_convenios, selected_segmentos),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/ciclo_dia/status/<job_id>")
def ciclo_dia_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job não encontrado."}), 404
    resp: dict = {"status": job["status"], "log": job.get("log", [])}
    if job["status"] == "error":
        resp["error"] = job.get("error", "")
    if job["status"] == "done":
        resp["relatorio"] = job.get("relatorio", {})
    return jsonify(resp)


# ── TESTE DE ESTEIRA (temporário) — medir descoberta+download paralelos ───────
# Disparo:    /admin/esteira/run?key=...&data=DD/MM/AAAA&m=4&n=2
# Acompanhar: /admin/esteira/log/<job_id>?key=...
_esteira_jobs: dict = {}
_ESTEIRA_KEY = os.environ.get("ESTEIRA_KEY", "rb-esteira-2026")


@app.route("/admin/esteira/run")
def _esteira_run():
    if request.args.get("key") != _ESTEIRA_KEY:
        return jsonify({"error": "forbidden"}), 403
    data = request.args.get("data")
    if not data:
        return jsonify({"error": "faltou ?data=DD/MM/AAAA"}), 400
    m = int(request.args.get("m", 6))
    n = int(request.args.get("n", 3))
    k = int(request.args.get("k", 5))
    k2 = int(request.args.get("k2", 0))           # pool de anexação (0 = sem anexar)
    dry = request.args.get("dry", "1") != "0"     # dry=0 => anexação REAL
    gkey = request.args.get("gkey") or os.environ.get("GEMINI_API_KEY")
    jid = uuid.uuid4().hex[:8]
    review_dir = f"/tmp/esteira_rev/{jid}"
    job = {"log": [], "done": False, "resumo": None, "error": None, "review_dir": review_dir}
    _esteira_jobs[jid] = job

    def _go():
        try:
            from esteira import rodar_esteira
            job["resumo"] = rodar_esteira(data, m, n, k, lambda msg: job["log"].append(msg),
                                          gemini_key=gkey, review_dir=review_dir,
                                          k_attach=k2, dry_run=dry)
            if not dry and k2 > 0 and job["resumo"]:   # só persiste execução REAL
                try:
                    job["execucao_id"] = db.salvar_execucao(job["resumo"])
                    job["log"].append(f"(execução salva: #{job['execucao_id']})")
                except Exception as e:
                    job["log"].append(f"(falha ao salvar execução: {str(e)[:100]})")
        except Exception as e:
            job["error"] = str(e)
            job["log"].append(f"ERRO: {e}")
        finally:
            job["done"] = True

    threading.Thread(target=_go, daemon=True).start()
    return jsonify({"job": jid, "acompanhar": f"/admin/esteira/log/{jid}?key={_ESTEIRA_KEY}"})


@app.route("/admin/esteira/log/<jid>")
def _esteira_log(jid: str):
    if request.args.get("key") != _ESTEIRA_KEY:
        return jsonify({"error": "forbidden"}), 403
    job = _esteira_jobs.get(jid)
    if not job:
        return jsonify({"error": "job não encontrado"}), 404
    return jsonify({"done": job["done"], "error": job["error"],
                    "resumo": job["resumo"], "log": job["log"][-300:]})


@app.route("/admin/esteira/revisao/<jid>")
def _esteira_revisao(jid: str):
    if request.args.get("key") != _ESTEIRA_KEY:
        return "forbidden", 403
    job = _esteira_jobs.get(jid)
    if not job or not job.get("resumo"):
        return "job não encontrado ou ainda rodando", 404
    r = job["resumo"]
    _ord = {"auto": 0, "justificativa": 1, "revisao": 2}
    decs = sorted(r.get("decisoes", []), key=lambda x: (_ord.get(x.get("categoria"), 3), x.get("gto")))
    h = ["<html><head><meta charset=utf-8><title>Revisão</title><style>",
         "body{font-family:sans-serif;background:#111;color:#eee;margin:18px}",
         ".card{border:1px solid #333;border-radius:8px;padding:12px;margin:12px 0;background:#1a1a1a}",
         ".auto{border-left:6px solid #2ecc71}.justificativa{border-left:6px solid #3498db}.revisao{border-left:6px solid #e67e22}",
         "img,embed{max-height:360px;max-width:47%;border:2px solid #333;margin:4px;background:#fff;vertical-align:top;cursor:zoom-in}",
         ".chosen{border:4px solid #2ecc71}.h{color:#999;font-size:13px;margin:4px 0}b{color:#fff}",
         ".aviso{background:#222;border:1px solid #444;padding:10px;border-radius:6px;color:#ddd}",
         "#lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.93);z-index:99;text-align:center;cursor:zoom-out}",
         "#lb img{max-height:96vh;max-width:96vw;margin-top:2vh;border:0}",
         ".btn{display:inline-block;background:#c0392b;color:#fff;padding:8px 14px;border-radius:6px;text-decoration:none;margin:6px 0}"
         "</style></head><body>",
         f"<h2>Revisão — {r.get('data')} | {r.get('solic_auto')} solic-auto / "
         f"{r.get('justificativa')} c/ justificativa / {r.get('revisao')} revisão</h2>",
         f"<a class='btn' href='/admin/esteira/descartar/{jid}?key={_ESTEIRA_KEY}' "
         "onclick=\"return confirm('Apagar os arquivos desta revisão?')\">🗑️ Descartar arquivos</a>",
         "<div class='aviso'>⚠️ O que é anexado no RedeUna: <b>laudo + imagens</b> (sempre) e, "
         "<b>só quando a GTO não tem justificativa</b>, a <b>solicitação destacada em verde</b>. "
         "Os outros anexos abaixo são apenas o que o Gemini analisou pra escolher — <b>NENHUM deles é "
         "anexado</b>. (Clique numa imagem pra ampliar; PDF use o '🔍 abrir'.)</div>"]
    import re as _re
    from datetime import date as _date

    def _fdate(s):
        m = _re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", str(s))
        try:
            return _date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
        except Exception:
            return None
    _dm = _re.findall(r"\d+", r.get("data") or "")
    dexm = None
    if len(_dm) >= 3:
        try:
            dexm = _date(int(_dm[2]), int(_dm[1]), int(_dm[0]))
        except Exception:
            dexm = None
    for x in decs:
        g = x.get("gemini") or {}
        cat = x.get("categoria", "revisao")
        if cat == "auto":
            tag_dec = f"→ <span style='color:#2ecc71'>ANEXA solicitação: {x.get('solicitacao')}</span>"
        elif cat == "justificativa":
            tag_dec = "→ <span style='color:#3498db'>GTO TEM JUSTIFICATIVA — só laudo+imgs (solicitação dispensada)</span>"
        else:
            tag_dec = "→ <span style='color:#e67e22'>REVISÃO humana</span>"
        h.append(f"<div class='card {cat}'><b>GTO {x['gto']} — {x['paciente']}</b> {tag_dec}")
        h.append(f"<div class='h'>exames GTO: {x.get('gto_exames')} &nbsp;|&nbsp; Gemini: "
                 f"tipo={g.get('tipo')} legível={g.get('legivel')} batem={g.get('exames_batem')} "
                 f"conf={g.get('confianca')}<br>lidos: {g.get('exames_lidos')}<br>"
                 f"motivo: {g.get('motivo') or x.get('erro') or ''}</div>")
        ocultos = 0
        for c in x.get("candidatos", []):
            if not c.get("arquivo"):
                continue
            is_chosen = c.get("idx") == x.get("solic_idx")
            cdate = _fdate(c["nome"])
            # esconde candidatos antigos (>90 dias da data do exame), exceto o escolhido
            if (not is_chosen) and dexm and cdate and abs((dexm - cdate).days) > 90:
                ocultos += 1
                continue
            chosen = "chosen" if is_chosen else ""
            src = f"/admin/esteira/arquivo/{jid}/{x['gto']}/{c['arquivo']}?key={_ESTEIRA_KEY}"
            tg = "embed" if str(c["arquivo"]).lower().endswith(".pdf") else "img"
            dtxt = f" · {cdate.strftime('%d/%m/%Y')}" if cdate else ""
            h.append(f"<span style='display:inline-block;width:47%;text-align:center'>"
                     f"<{tg} class='{chosen}' src='{src}'>"
                     f"<div class='h'>[{c['idx']}] {c['nome']}{dtxt} · "
                     f"<a href='{src}' target='_blank' style='color:#3498db'>🔍 abrir</a></div></span>")
        if ocultos:
            h.append(f"<div class='h'>🕓 {ocultos} solicitação(ões) antiga(s) ocultada(s) "
                     f"(fora da data do exame — quero só as atuais)</div>")
        h.append("</div>")
    h.append("<div id=lb onclick=\"this.style.display='none'\"><img id=lbi></div>")
    h.append("<script>document.addEventListener('click',function(e){"
             "if(e.target.tagName=='IMG'&&e.target.id!='lbi'){"
             "document.getElementById('lbi').src=e.target.src;"
             "document.getElementById('lb').style.display='block';}});</script>")
    h.append("</body></html>")
    return "".join(h)


@app.route("/admin/esteira/descartar/<jid>")
def _esteira_descartar(jid: str):
    if request.args.get("key") != _ESTEIRA_KEY:
        return "forbidden", 403
    job = _esteira_jobs.get(jid)
    base = (job or {}).get("review_dir") or f"/tmp/esteira_rev/{jid}"
    import shutil
    shutil.rmtree(base, ignore_errors=True)
    if job:
        job["resumo"] = None  # libera memória do resumo também
    return "Arquivos descartados. ✓ (pode fechar a aba)"


@app.route("/admin/esteira/arquivo/<jid>/<gto>/<path:fname>")
def _esteira_arquivo(jid: str, gto: str, fname: str):
    if request.args.get("key") != _ESTEIRA_KEY:
        return "forbidden", 403
    job = _esteira_jobs.get(jid)
    if not job:
        return "404", 404
    base = job.get("review_dir") or f"/tmp/esteira_rev/{jid}"
    path = os.path.realpath(os.path.join(base, str(gto), fname))
    if not path.startswith(os.path.realpath(base)) or not os.path.exists(path):
        return "404", 404
    return send_file(path)


if __name__ == "__main__":
    # Dev local. Em produção (Docker/EasyPanel) o servidor é o gunicorn.
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=False, host="0.0.0.0", port=port)
