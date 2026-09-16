# Imagem oficial do Playwright (já traz Chromium + libs de sistema).
# Versão casada com o playwright do requirements (1.58.x).
FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5000

# Tesseract (OCR das solicitações). A língua 'por' vem do tessdata/ do projeto
# via TESSDATA_PREFIX, então basta o binário aqui.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Tailscale (binarios estaticos oficiais). Usado so quando TS_AUTHKEY existe:
# saida do OdontoPrev pelo computador da clinica (exit node). Ver entrypoint.sh.
ARG TAILSCALE_VERSION=1.102.4
RUN python3 -c "import urllib.request as u; u.urlretrieve('https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_amd64.tgz', '/tmp/ts.tgz')" \
    && tar -xzf /tmp/ts.tgz -C /tmp \
    && mv /tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscale /tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscaled /usr/local/bin/ \
    && rm -rf /tmp/ts.tgz /tmp/tailscale_${TAILSCALE_VERSION}_amd64 \
    && tailscale version

# Dependências Python (a imagem já tem playwright + browsers instalados)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação
COPY . .

EXPOSE 5000

# entrypoint.sh sobe o Tailscale (se TS_AUTHKEY existir) e depois o gunicorn com
# os MESMOS parametros de antes. `sed` garante fim de linha LF (repo editado no
# Windows): com CRLF o /bin/sh nao acha o interpretador e o container nao sobe.
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh
CMD ["/bin/sh", "/app/entrypoint.sh"]
