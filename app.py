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
from config import CONVENIOS, SEGMENTOS

# Inicializa o banco (cria tabelas se não existirem). Falha não derruba o app.
try:
    db.init_db()
    app.logger.info("Banco inicializado (%s).", db.DATABASE_URL.split("@")[-1])
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
        resp["error"] = job.get("error", "")
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


if __name__ == "__main__":
    # Dev local. Em produção (Docker/EasyPanel) o servidor é o gunicorn.
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=False, host="0.0.0.0", port=port)
