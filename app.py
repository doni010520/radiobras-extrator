"""
RADIOBRAS — Extrator Web
Flask app: relatório analítico (xlsx) + download de arquivos (ZIP).
"""

import io
import logging
import os
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

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Todas as unidades REDE UNNA existentes no select 'insurance' do SmartRIS
# (descoberto ao vivo em 01/06/2026 — 7 convênios com "UNNA").
CONVENIOS = [
    "REDE UNNA - CENTRO",
    "REDE UNNA - ITAIGARA",
    "REDE UNNA - PERIPERI",
    "REDE UNNA - LAURO DE FREITAS",
    "REDE UNNA - CAMAÇARI",
    "REDE UNNA CAMINHO DAS ÁRVORES - TANCREDO",
    "REDE UNNA DESCONTO CAMAÇARI",
]
# Todos os segmentos (necessário pois o relatório filtra por insurance E segments).
SEGMENTOS = ["BRASMED", "CAMAÇARI", "CENTRO", "ITAIGARA", "LAURO", "PERIPERI", "TANCREDO"]

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


# ── Rotas ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", convenios=CONVENIOS, segmentos=SEGMENTOS)


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
