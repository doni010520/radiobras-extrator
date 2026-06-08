# Deploy — VPS via EasyPanel

App Flask + Playwright (PRORADIS/SmartRIS + OdontoPrev). Roda em container Docker.

## Pré-requisitos
- VPS com EasyPanel instalado.
- Repositório no GitHub (este).

## Passo a passo no EasyPanel
1. **Create → App** (Project a seu critério).
2. **Source: GitHub** → selecione o repositório e a branch (`master` após o merge, ou `feat/...`).
3. **Build: Dockerfile** (o EasyPanel detecta o `Dockerfile` na raiz).
4. **Environment variables** — adicione (Settings → Environment):
   ```
   SMARTRIS_EMAIL=<email do PRORADIS>
   SMARTRIS_PASSWORD=<senha do PRORADIS>
   ODONTOPREV_USER=<código do credenciado OdontoPrev>
   ODONTOPREV_PASSWORD=<senha do OdontoPrev>
   ```
   (Essas variáveis sobrescrevem qualquer valor padrão no código.)
5. **Port / Proxy**: o container expõe **5000**. Configure o domínio/porta no EasyPanel apontando para `5000`.
6. **Deploy**. O build instala as dependências sobre a imagem do Playwright (Chromium já incluso).
7. **Recursos**: reserve memória suficiente (Chromium headless ≈ 0,5–1 GB por execução). Recomendado ≥ 2 GB de RAM no serviço.

## Observações técnicas
- **1 worker** (gunicorn `--workers 1`): o controle de jobs é em memória; múltiplos workers quebrariam o polling de status. Concorrência via `--threads`.
- **`--timeout 0`**: a extração de um dia pode levar minutos.
- Chromium roda com `--no-sandbox --disable-dev-shm-usage` (necessário em container).
- Os arquivos baixados são temporários (ZIP em memória / pasta `_tmp_*` apagada ao fim). Nada de dado de paciente é persistido em disco entre execuções.

## Atualização
Push na branch → **Deploy** no EasyPanel (ou auto-deploy se configurado o webhook do GitHub).
