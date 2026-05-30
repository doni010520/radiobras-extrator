"""
RADIOBRAS — Extrator Web
Flask app: serve o formulário e executa o extrator.
"""

import io
import logging
import os
import sys
import traceback

from flask import Flask, jsonify, render_template, request, send_file

sys.path.insert(0, os.path.dirname(__file__))

from extrator_pacientes_analitico import (
    discover_tokens_and_cookies,
    get_credentials,
    parse_html_to_df,
    post_relatorio,
    resolve_tokens,
)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CONVENIOS = [
    "REDE UNNA - CENTRO",
    "REDE UNNA - ITAIGARA",
    "REDE UNNA - PERIPERI",
    "REDE UNNA - LAURO DE FREITAS",
]
SEGMENTOS = ["CENTRO", "ITAIGARA", "LAURO", "PERIPERI"]


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

        insurance_tokens = resolve_tokens(selected_convenios, convenio_map, "convênio")
        segment_tokens = resolve_tokens(selected_segmentos, segmento_map, "segmento")

        if not insurance_tokens:
            return jsonify({"error": "Nenhum convênio resolvido. Verifique os nomes."}), 400

        html = post_relatorio(cookies, insurance_tokens, segment_tokens, date_from, date_to)
        df, valor_total, num_exames = parse_html_to_df(html)

        if df.empty:
            return jsonify({
                "warning": "Nenhum exame encontrado para o período e filtros selecionados."
            }), 200

        # Gerar xlsx em memória para não poluir o diretório
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        buf.seek(0)

        date_tag = date_from.replace("/", "") + "_" + date_to.replace("/", "")
        filename = f"pacientes_analitico_REDEUNNA_{date_tag}.xlsx"

        return send_file(
            buf,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as exc:
        tb = traceback.format_exc()
        app.logger.error("Erro em /gerar:\n%s", tb)
        return jsonify({"error": str(exc), "traceback": tb}), 500


if __name__ == "__main__":
    app.run(debug=False, port=5000)
