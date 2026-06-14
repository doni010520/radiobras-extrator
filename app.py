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


def _run_fechar_job(job_id: str, data: str, dry_run: bool) -> None:
    """Job do 'Fechar dia' (orquestrador completo fechar_dia.py)."""
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    def progress(msg: str) -> None:
        with _jobs_lock:
            _jobs[job_id].setdefault("log", []).append(str(msg))

    # Registra a execução no histórico (não bloqueia se o banco falhar).
    run_id = None
    try:
        run_id = db.criar_run(data, dry_run)
    except Exception as e:
        app.logger.error("Falha ao criar run no banco: %s", e)

    try:
        relatorio = fechar_dia(data, CONVENIOS, SEGMENTOS,
                               dry_run=dry_run, progress_cb=progress)
        if run_id is not None:
            try:
                db.finalizar_run_ok(run_id, relatorio)
            except Exception as e:
                app.logger.error("Falha ao salvar run %s: %s", run_id, e)
        with _jobs_lock:
            _jobs[job_id].update({"status": "done", "relatorio": relatorio})
    except Exception as exc:
        tb = traceback.format_exc()
        app.logger.error("Erro no fechar_dia %s:\n%s", job_id, tb)
        if run_id is not None:
            try:
                db.finalizar_run_erro(run_id, str(exc))
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


@app.route("/relatorio")
def index():
    """Tela antiga (relatório analítico xlsx + download ZIP)."""
    return render_template("index.html", convenios=CONVENIOS, segmentos=SEGMENTOS)


# ── APIs do Dashboard ─────────────────────────────────────────────────────────

@app.route("/api/dashboard")
def api_dashboard():
    """Dados agregados p/ o dashboard: última execução, semana, fila, totais."""
    try:
        ultima = db.run_mais_recente()
        return jsonify({
            "ultima": ultima,
            "recentes": db.ultimas_runs(8),
            "semana": db.serie_semana(),
            "revisao": db.fila_revisao(30),
            "totais": db.totais_gerais(),
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


@app.route("/fechar", methods=["POST"])
def fechar_route():
    """Inicia o fechamento do dia (download + anexo no OdontoPrev). Assíncrono."""
    data = request.form.get("data", "").strip()
    # 'simular' = dry-run (não anexa, só mostra o que faria)
    dry_run = request.form.get("simular", "").lower() in ("1", "true", "on", "yes")
    if not data:
        return jsonify({"error": "Informe o dia (DD/MM/AAAA)."}), 400
    if not re.match(r"^\d{2}/\d{2}/\d{4}$", data):
        return jsonify({"error": "Data inválida. Use DD/MM/AAAA."}), 400

    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "log": []}
    threading.Thread(
        target=_run_fechar_job, args=(job_id, data, dry_run), daemon=True
    ).start()
    return jsonify({"job_id": job_id, "dry_run": dry_run})


@app.route("/fechar/status/<job_id>")
def fechar_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job não encontrado."}), 404
    resp: dict = {"status": job["status"], "log": job.get("log", [])}
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
