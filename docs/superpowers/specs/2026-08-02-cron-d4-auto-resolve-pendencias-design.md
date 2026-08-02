# Cron diário D-4 que fatura o dia e re-tenta as pendências abertas

**Data:** 2026-08-02
**Escopo:** projeto isolado (Feature 2 de 3). Não inclui a tela de Pendências.
**Estado de entrega:** código pronto e testado, mas o cron fica **DESATIVADO**
(`FATURAR_CRON=0`, o default) até o proxy do OdontoPrev voltar — senão ele só
falharia todo dia às 5h.

## Objetivo

Todo dia, de forma automática, o sistema deve:
1. Faturar o dia **D-4** (quatro dias atrás) nas três unidades.
2. **Re-tentar** todos os dias que ainda têm pendência ABERTA dentro do prazo —
   pode ter saído laudo, a clínica pode ter anexado o pedido, o cadastro pode ter
   sido corrigido. Se o usuário NÃO marcou a pendência como resolvida à mão, o
   cron tenta matá-la sozinho.

## Situação atual (o que já existe e o que está quebrado)

`app.py::_faturar_cron_body` já faz quase tudo:
- Já une o dia-alvo com `db.dias_com_pendencia_aberta(prazo)` (a re-tentativa de
  pendências JÁ está desenhada).
- Quando uma guia fatura no reprocessamento, a pendência dela fecha sozinha
  (`resolvido_por="sistema"`, em `_sync_pendencias` dentro de `salvar_execucao`);
  pendência resolvida à mão nunca reabre; dia fora do prazo não é re-tentado.

Dois defeitos impedem que funcione:
1. **Bug do `_j` (crítico).** Linhas 804 e 812: `db.salvar_execucao(resumo,
   (_j or {}).get('log'))` — a variável `_j` **não existe**. Levanta `NameError`,
   que é engolido, e a execução do cron **nunca é gravada**. Consequência: as
   pendências **nunca fecham** pelo cron (o `_sync_pendencias` está dentro do
   `salvar_execucao` que nunca roda), o resumo semanal não vê os faturamentos, e o
   log diz "0 faturada(s)" mesmo tendo anexado. Ou seja: hoje a re-tentativa de
   pendências é **inócua**.
2. **É D-3, deveria ser D-4.** Linha 784: `hoje - timedelta(days=3)`.

## O que muda

Arquivo `app.py`, função `_faturar_cron_body` — cirúrgico:

1. **D-4:** extrair `_dia_alvo_cron(hoje) -> "DD/MM/AAAA"` (= `hoje - 4 dias`),
   testável isoladamente, e usar no lugar do inline `days=3`.
2. **Conserto do `_j`:** capturar o log da execução num acumulador local
   (`_logs = []`, `log=_logs.append`) e passar `"\n".join(_logs)` para
   `salvar_execucao` / `salvar_execucao_falha`. Isso remove o `NameError` E passa
   a persistir o log (hoje descartado pelo `log=lambda m: None`). Com o
   `salvar_execucao` rodando, o `_sync_pendencias` fecha as pendências
   resolvidas — a re-tentativa passa a funcionar de verdade.
3. **Nada muda no gatilho de ligar/desligar:** o scheduler continua atrás de
   `FATURAR_CRON` (default `0`). Fica pronto e DESATIVADO. Ligar = setar
   `FATURAR_CRON=1` no EasyPanel quando o proxy voltar.

## O que NÃO muda

- A união com `dias_com_pendencia_aberta(prazo)` (a re-tentativa) já existe e está
  correta — só passa a ter efeito porque o `salvar_execucao` volta a rodar.
- O alerta de SLA por e-mail no fim do cron (`_enviar_alertas_sla`) segue igual.
- A trava de concorrência (`_esteira_reservar`/`_liberar`) segue igual.
- Não mexe na esteira, na anexação nem no fallback de nome (Feature 1).

## Testes (TDD)

1. **`_dia_alvo_cron` é D-4:** `_dia_alvo_cron(date(2026,8,2)) == "29/07/2026"`
   (e um caso que atravessa o mês).
2. **Cron grava a execução (bug do `_j` morto):** com `rodar_esteira` e o `db`
   mockados, `_faturar_cron_body()` chama `salvar_execucao` com o resumo — antes o
   `NameError` fazia nada ser gravado. Assert: `salvar_execucao` foi chamado 1x
   com o resumo retornado.
3. **Cron grava a FALHA sem estourar:** quando `rodar_esteira` levanta exceção,
   `salvar_execucao_falha` é chamado (sem o `NameError` do 2º `_j`).

## Fora de escopo

- Ligar o cron (fica desativado por decisão do dono).
- A tela de Pendências (Feature 3).
