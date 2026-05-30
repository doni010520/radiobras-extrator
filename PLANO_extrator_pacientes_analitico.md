# PLANO DE IMPLEMENTAÇÃO — Extrator "Pacientes (analítico)" SmartRIS RADIOBRAS

> Documento de especificação para o **Claude executor**. Foi feito reconhecimento ao vivo do sistema; tudo abaixo está confirmado. Implementar exatamente como descrito. NÃO é preciso refazer descoberta.

## 1. Objetivo (escopo travado)

Extrair o relatório **Pacientes (analítico)** do módulo `admin_reports`, replicando a configuração do usuário:

| Parâmetro | Valor |
|---|---|
| Tipo de relatório | `patients_detailed_report` (Pacientes analítico) |
| Período (Início / Fim) | parametrizável — padrão `28/05/2026` a `28/05/2026` |
| Tipo de data (`datetype`) | `realized` (Realizado) |
| Convênios | **REDE UNNA - CENTRO**, **REDE UNNA - ITAIGARA**, **REDE UNNA - PERIPERI**, **REDE UNNA - LAURO DE FREITAS** |
| Segmentos | **CENTRO**, **ITAIGARA**, **LAURO**, **PERIPERI** |

**Saída:** arquivo `.xlsx` com as linhas da tabela do relatório.

## 2. Ambiente já instalado
Python 3.13 com: `playwright` (chromium instalado), `requests`, `httpx`, `beautifulsoup4`, `lxml`, `pandas`, `openpyxl`.

## 3. Credenciais
- URL base: `https://radiobras.smartris.com.br/ris/`
- username: `***REMOVED***`
- password: `***REMOVED***`
- (Sugestão: ler de variáveis de ambiente / arquivo `.env`, não hardcodar.)

## 4. Fatos técnicos confirmados (NÃO mudar)

### 4.1 Stack
PHP/CodeIgniter + Apache. Página de relatórios é **Vue.js** → precisa renderizar no navegador (Playwright) para os selects popularem. Cookies de sessão: `proradis_session` + `PHPSESSID`.

### 4.2 Login
1. `GET /ris/` → no HTML há `<input type="hidden" name="csrf_token" value="...">`. Extrair.
2. `POST /ris/login/checklogin` com form-urlencoded: `username`, `password`, `csrf_token`.
3. Sucesso → redireciona (302) para área logada. Manter cookies da sessão.

### 4.3 ⚠️ Tokens criptografados por sessão (PONTO CRÍTICO)
No módulo `admin_reports`, os `value` dos `<option>` de **convênio** e **segmento** NÃO são numéricos. São tokens cifrados no padrão:
```
wEWorVa4iBJ37as3F3-_pp_pp_-Itd4yEMWxW4d...-_ll_ll_-..._-TA_TA-_
```
**Foi testado: esses tokens MUDAM a cada novo login.** Portanto:
- ❌ NÃO hardcodar IDs/tokens.
- ✅ A cada execução: abrir a página, ler os `<option>` (texto visível + value atual) e montar um dicionário `{nome_legível: token_da_sessão}`.
- O **texto** ("REDE UNNA - CENTRO") é a chave estável. O **value** (token) é descartável.

### 4.4 Selects com id aleatório
Os `<select>` recebem `id` aleatório a cada carga (ex.: `sel1GM`). **Usar sempre o atributo `name`**, que é estável:
- Tipo de relatório → `name="r1"`
- Convênio → `name="insurance"`
- Segmento → `name="segments"`
- Tipo de data → `name="datetype"`
- (Excluir convênio → `name="insurance_exclude"`; Plano → `name="global_plan"`; Modalidade → `name="modality"` — não usados neste escopo.)

### 4.5 Endpoint de geração (confirmado por captura)
```
POST https://radiobras.smartris.com.br/ris/admin_reports/patients_detailed_report/
Content-Type: application/x-www-form-urlencoded

order_by=&from=28/05/2026&to=28/05/2026&datetype=realized
&insurance=<tok1>,<tok2>,<tok3>,<tok4>
&segments=<tokA>,<tokB>,<tokC>,<tokD>
```
- `insurance` e `segments`: tokens da sessão, **separados por vírgula** (URL-encoded como `%2C`).
- Datas no formato **DD/MM/YYYY**.
- Resposta: **HTML** (não JSON), status 200, contendo um `<table>`.

### 4.6 Estrutura da resposta (colunas confirmadas)
Tabela com cabeçalhos, nesta ordem:
```
Segmento | Cód. Pac | Paciente | Data | Solicitante | Convênio | Pedido | Autorização | Procedimento | Valor recebido (R$)
```
Rodapé com totais: "Valor total: R$ X" e "Nº de exames: N".

## 5. Estratégia de implementação recomendada (HÍBRIDA)

Como a página é Vue e os tokens são por-sessão, a forma mais robusta:

**Opção A (recomendada) — Playwright para descobrir tokens + requests para o POST:**
1. Login via Playwright (ou via requests + csrf; mas Playwright simplifica a sessão).
2. `goto /ris/admin_reports`, esperar render (networkidle + ~2-3s).
3. Selecionar o tipo de relatório no select `name="r1"` com value `patients_detailed_report` e disparar `change` → aguarda `admin_reports/load_filters` renderizar os filtros.
4. Ler options de `name="insurance"` e `name="segments"` → montar mapas `{texto: value}`.
5. Resolver os 4 convênios e 4 segmentos do escopo para seus tokens.
6. Extrair os cookies da sessão do Playwright (`context.cookies()`).
7. Com `requests`/`httpx` usando esses cookies, fazer o `POST` do item 4.5.
8. Parsear o HTML da resposta com BeautifulSoup → DataFrame → `.xlsx`.

**Opção B (mais simples, tudo no Playwright):** após montar os filtros, clicar no botão **"Gerar relatório"** e capturar o HTML da resposta (interceptar a response do POST). Ou clicar em **"Gerar arquivo"** e capturar o download (formato a confirmar — provável xlsx/csv). *Recomendo o executor testar "Gerar arquivo" primeiro: se já entrega xlsx pronto, dispensa o parsing.*

## 6. Parsing → Excel
- BeautifulSoup: localizar a `<table>` de dados, ler `<thead>` (10 colunas acima) e `<tbody>` linhas.
- Limpar entidades HTML (`&#186;` etc.), normalizar valores monetários ("R$ 1.234,56" → float se desejado, ou manter string).
- `pandas.DataFrame` → `to_excel("pacientes_analitico_REDEUNNA_<data>.xlsx", index=False)`.
- Linha de totais do rodapé: registrar em log/console (não misturar com as linhas de dados).

## 7. Robustez (adicionar por último)
- Re-login automático se a sessão expirar (detectar redirect para tela de login / `monitor/instance` status).
- Timeout e 1-2 retries no POST.
- Validar que a tabela tem linhas; se 0 linhas, avisar (pode ser que não haja exames no período).
- Respeitar o sistema: 1 requisição por vez, sem paralelismo agressivo.

## 8. Teste de aceitação
1. Rodar com período `28/05/2026`–`28/05/2026`, os 4 convênios REDE UNNA e 4 segmentos.
2. Conferir que o `.xlsx` tem as 10 colunas na ordem do item 4.6.
3. Conferir que o "Valor total" e "Nº de exames" do rodapé batem com a soma das linhas.

## 9. Pontos a confirmar pelo executor (deixados em aberto de propósito)
- Endpoint/formato exato do botão **"Gerar arquivo"** (download direto) — pode simplificar tudo.
- Nomes EXATOS dos campos de data no DOM (no print são "Início"/"Fim"; no POST viraram `from`/`to`). Setar via os inputs de data e disparar `input`/`change`, ou montar o POST manualmente com `from`/`to` (mais confiável).
