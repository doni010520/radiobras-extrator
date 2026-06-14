# Deploy — Render / EasyPanel

App Flask + Playwright + Tesseract (PRORADIS/SmartRIS + OdontoPrev). Roda em
container Docker.

## O que a aplicação faz (uso de 1 clique)
Tela principal (`/`): o usuário escolhe a **data** e clica em **FECHAR DIA**. O
sistema baixa laudos + imagens do PRORADIS e anexa em cada GTO em "análise de
repasse" do OdontoPrev, mostrando o progresso ao vivo e uma tabela final por GTO.
O upload é **idempotente** (reexecutar o mesmo dia não duplica anexos). Há a opção
**Simular** (dry-run): mostra o que faria sem anexar nada.
A tela antiga (relatório analítico `.xlsx` + download `.zip`) fica em `/relatorio`.

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
   DATABASE_URL=<connection string do Supabase/Postgres>
   ```
   - As credenciais são **obrigatórias** (o app falha sem elas — não há mais fallback no código).
   - `DATABASE_URL`: liga o histórico do dashboard a um Postgres durável (recomendado:
     Supabase). **Sem** essa variável o app cai em SQLite local — que é apagado a cada
     redeploy do container, então o histórico se perde. Para produção, **defina-a**.

### Como obter a DATABASE_URL no Supabase
1. supabase.com → New project (guarde a senha do banco).
2. Project → **Settings → Database → Connection string → URI**.
3. Copie a URI (`postgresql://postgres:[SENHA]@db.<ref>.supabase.co:5432/postgres`),
   troque `[SENHA]` pela senha do banco e cole em `DATABASE_URL` no EasyPanel.
   (Para pooling/IPv4, use a porta **6543** do "Connection pooler" se o host direto não conectar.)
   As tabelas são criadas automaticamente no primeiro boot.
5. **Port / Proxy**: o container expõe **5000**. Configure o domínio/porta no EasyPanel apontando para `5000`.
6. **Deploy**. O build instala o Tesseract (apt) e as dependências Python sobre a imagem do Playwright (Chromium já incluso).
7. **Recursos**: reserve memória suficiente (Chromium headless + Tesseract ≈ 0,5–1 GB por execução). Recomendado ≥ 2 GB de RAM no serviço.

## Deploy no Render (alternativa)
1. **New → Web Service** → conecte o repositório do GitHub.
2. **Runtime: Docker** (usa o `Dockerfile` da raiz).
3. **Environment**: adicione as mesmas 4 variáveis acima (SMARTRIS_*, ODONTOPREV_*).
4. **Instance type**: escolha um plano com **≥ 2 GB RAM** (o headless+OCR estoura 512 MB).
5. O Render injeta a variável `PORT`; o app já a respeita. Não precisa configurar porta manualmente.
6. **Importante (1 worker)**: o job é em memória. O Dockerfile já fixa `gunicorn --workers 1`; não aumente o número de workers ou o polling de status quebra. Para escala, use `--threads`.

## Observações técnicas
- **1 worker** (gunicorn `--workers 1`): o controle de jobs é em memória; múltiplos workers quebrariam o polling de status. Concorrência via `--threads`.
- **`--timeout 0`**: a extração de um dia pode levar minutos.
- Chromium roda com `--no-sandbox --disable-dev-shm-usage` (necessário em container).
- Os arquivos baixados são temporários (ZIP em memória / pasta `_tmp_*` apagada ao fim). Nada de dado de paciente é persistido em disco entre execuções.

## Atualização
Push na branch → **Deploy** no EasyPanel (ou auto-deploy se configurado o webhook do GitHub).
