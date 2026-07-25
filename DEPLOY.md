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
   GEMINI_API_KEY=<chave da API do Gemini>
   SECRET_KEY=<string aleatória longa, ver abaixo>
   ODONTO_PROXY_URL=http://usuario:senha@host:porta
   ```
   - As credenciais são **obrigatórias** (o app falha sem elas — não há mais fallback no código).
   - `DATABASE_URL`: liga o histórico do dashboard a um Postgres durável (recomendado:
     Supabase). **Sem** essa variável o app cai em SQLite local — que é apagado a cada
     redeploy do container, então o histórico se perde. Para produção, **defina-a**.
   - `GEMINI_API_KEY`: **sem ela o estágio de leitura não roda** — nenhuma solicitação é
     identificada e o dia inteiro cai em pendência.
   - `SECRET_KEY`: assina o cookie de sessão. **Sem ela o app gera uma chave aleatória a
     cada restart** e todo mundo é deslogado a cada deploy. Gere com:
     `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
     Nunca reaproveite uma chave que já tenha aparecido em repositório público.
   - `ODONTO_PROXY_URL`: **crítico e fácil de esquecer numa migração de VPS.** O
     OdontoPrev bloqueia IP de datacenter (rate-limit/anti-bot), então o login passa por
     um proxy residencial brasileiro *sticky*. O PRORADIS é acessado direto, por isso a
     variável é só do Odonto. Formato: `http://usuario:senha@host:porta`; se o usuário
     tiver `;sessid.<x>`, o código troca o token a cada sessão sozinho.
     **Sem essa variável, o faturamento simplesmente não loga no portal.**

### Trocando de VPS — o que levar junto
Todas as variáveis acima. O que costuma quebrar quando se esquece:

| Variável | Se faltar |
|---|---|
| `ODONTO_PROXY_URL` | Login no OdontoPrev falha (IP novo é bloqueado). **Nada fatura.** |
| `GEMINI_API_KEY` | Nenhuma solicitação é lida. Tudo vira pendência. |
| `SECRET_KEY` | Sessões caem a cada deploy. |
| `DATABASE_URL` | Histórico e pendências somem (cai em SQLite efêmero). |

O banco é externo (Supabase), então **nada de dado precisa ser migrado** — basta apontar a
mesma `DATABASE_URL`. Depois de subir, confira nesta ordem: `/healthz` → login →
`/relatorios/dia` de um dia conhecido → um **DRY** em `/faturar` antes de qualquer
execução real.

### Agendadores (cron interno)
Ficam **desligados por padrão** — precisam ser ativados explicitamente:
```
FATURAR_CRON=1          # faturamento diário (hora: FATURAR_CRON_HOUR, default 5)
GLOSA_AUTO_UPDATE=1     # atualização do panorama de glosas
ANEXACAO_AUTO_UPDATE=1  # varredura do estado das GTOs
```
Ligue **um de cada vez**, observando uma execução antes do próximo: os três competem
pelo mesmo login do OdontoPrev e a soma deles já derrubou o container por memória.

### Alerta de prazo (SLA) por email — opcional, recomendado
O app envia um email diário (ao fim do cron de faturamento) com as GTOs não faturadas
**dentro de 2 dias do prazo** (vencidas, vence amanhã, faltam 2 dias). Sem SMTP, o
email pula limpo e a tela `/revisao` segue mostrando os alertas. Para ligar o email,
adicione (Settings → Environment):
```
SMTP_HOST=smtp.hostinger.com     # SMTP da Hostinger (ou outro provedor)
SMTP_PORT=587                    # 587 (STARTTLS). NÃO use 465 — o código faz STARTTLS.
SMTP_USER=alertas@seudominio.com.br
SMTP_PASSWORD=<senha da caixa>
SMTP_FROM=alertas@seudominio.com.br   # mesmo endereço (bate com SPF/DKIM do domínio)
ALERTA_EMAIL_TO=voce@seudominio.com.br  # destinatários, separados por vírgula
```
   - Criar a caixa no hPanel da Hostinger → **Emails**. Remetente do próprio domínio
     evita cair em spam (SPF/DKIM já configurados pela Hostinger).
   - `FATURAR_PRAZO_DIAS` (default **7**) = prazo de faturamento da OdontoPrev; é a base
     do cálculo de SLA. Desligar o email: `ALERTA_SLA=0`.
   - Testar depois do deploy (logado como admin): `POST /alerta/testar-email`.

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
