# Imagem oficial do Playwright (já traz Chromium + libs de sistema).
# Versão casada com o playwright do requirements (1.58.x).
FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5000

# Dependências Python (a imagem já tem playwright + browsers instalados)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação
COPY . .

EXPOSE 5000

# 1 worker (job store é em memória) + threads para o job em background.
# timeout 0 porque a extração de um dia pode levar minutos.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "0", \
     "--bind", "0.0.0.0:5000", "app:app"]
