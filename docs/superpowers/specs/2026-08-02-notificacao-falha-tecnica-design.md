# Notificação de falha técnica ao dono (falha técnica sai da tela do operador)

**Data:** 2026-08-02
**Status:** DESENHO PARADO — aguardando decisão do dono sobre **canal** e **cadência**
(respondeu "vemos depois" em 02/08). Retomar por aqui; o mapeamento abaixo já está
fundamentado no código real.

## Problema / decisão do dono

Uma **falha técnica** (problema do robô, não falta de documento) **não deve aparecer
para os operadores** — eles não têm o que fazer com ela. Deve ser **hidden da tela/badge**
e **notificada ao dono**. O cron continua re-tentando; o que virar laudo evapora sozinho.

## O que é "falha técnica" (determinístico)

As 4 chaves com responsável `"Nós"` em `db._GRUPOS_PENDENCIA`:
`nome_nao_bate`, `guia_ilegivel`, `anexacao`, `falha_tecnica` (classificadas por regex
sobre o `motivo`). **Furo conhecido:** o estouro de quota da API (429) gera o motivo
"leitura automática ficou indisponível", que NÃO casa nenhuma regex "Nós" e cai em
`outros`→Conferência — mas costuma ter `categoria=='erro'`. Portanto:

    _eh_tecnica(p) = (classificar_pendencia(p['motivo'])[1] == _NOSSO) or (p['categoria']=='erro')

Além das pendências por-guia, há o **aborto de execução inteira** (`salvar_execucao_falha`,
db.py:569) — login/proxy/Gemini fora. Também é falha técnica, e hoje ninguém é avisado.

## Infra que já existe (fundamentado — mapeamento 02/08)

- **Canal:** `_send_email(assunto, txt, html)` (app.py:617) — SMTP stdlib (Hostinger),
  genérico, destino = env única `ALERTA_EMAIL_TO` (aceita 1 endereço). **Reusável sem
  dependência nova.** Falha quieto (retorna False + log), nunca levanta.
- **DORMENTE:** `SMTP_*` e `ALERTA_EMAIL_TO` não estão no `.env` local (podem estar no
  EasyPanel). Sem eles, todo alerta é pulado. `/api/diag` expõe `smtp_configurado`.
- **Sem outro canal:** zero WhatsApp/uazapi/telegram/push/webhook de saída no repo.
  WhatsApp seria viável (só `requests.post` p/ uazapi — sem lib nova), mas exige
  instância + token + envs + pegadinha do 9º dígito. Serviço externo novo pra manter.
- **Dedup:** padrão "já enviei hoje" já existe (`cron_state.resumo_fat_last_at` +
  `_resumo_fat_enviado_hoje`, app.py:863). Chave única `pendencias(conta,dia,gto)`
  (db.py:1078) + auto-resolução (`resolvido_por='sistema'`, db.py:318) → base pronta
  pra "só o novo". **Não existe** flag `notificado` em tabela nenhuma (criar é barato
  via `_ensure_columns` ALTER TABLE ADD COLUMN IF NOT EXISTS).
- **Link pro caso:** `salvar_execucao_falha` retorna o `execucao_id`; rota
  `/relatorios/execucao/<id>/log` (app.py:1435) imprime "# ABORTOU: <erro>" + log.
  **Falta** `APP_BASE_URL` — os e-mails hoje usam caminhos relativos (não clicáveis);
  o cron roda fora de request context, então `url_for(_external=True)` não serve.

## Desenho proposto

1. **Some da frente dos operadores.** Persistir `responsavel`/`chave` como coluna nova
   no INSERT de `_sync_pendencias` (db.py:326) — mais robusto que reclassificar texto a
   cada leitura, e fecha o furo do 429. Badge (`contar_pendencias_abertas`) e a nova tela
   filtram `NOT tecnica`. Os técnicos ficam numa seção **"Fila técnica" só-admin** — nada
   silencioso, só fora da vista de quem não pode agir.
2. **Notifica o dono por e-mail** (reusa `_send_email` + `ALERTA_EMAIL_TO`):
   - **Aborto na hora:** hook em `salvar_execucao_falha` (cobre web /faturar E cron) →
     e-mail imediato com deep-link pro log. Dedup por `(conta,dia)`.
   - **Resumo técnico diário:** ao fim do `_faturar_cron_body`, UM e-mail com os técnicos
     NOVOS (nascidos desde a última notificação). Já avisado não repete; resolvido some.
   - Gate próprio `ALERTA_FALHA` (espelha `ALERTA_SLA`/`FATURAR_CRON`).

## Decisões PENDENTES (do dono)

- **Canal:** e-mail (pronto, grátis) vs + WhatsApp depois vs só WhatsApp. → recomendação: e-mail.
- **Cadência:** aborto-na-hora + resumo-diário (recomendado) vs só diário vs cada caso na hora.
- **Pré-requisito operacional:** confirmar/preencher `SMTP_*` + `ALERTA_EMAIL_TO` +
  `APP_BASE_URL` no EasyPanel (senão o e-mail é pulado em silêncio).

## Fora de escopo

- A tela de Pendências em si (spec/ mockup próprios). Este doc é só a NOTIFICAÇÃO +
  a regra de esconder o técnico do operador.
