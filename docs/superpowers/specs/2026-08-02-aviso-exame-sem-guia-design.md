# Alerta reconhecível: "exame pronto SEM GUIA"

**Data:** 2026-08-02
**Design aprovado pelo dono** (02/08): modal ao entrar + banner vermelho fixo;
só some quando o usuário marca **Ciente**; registra quem/quando.

## Problema

Quando um paciente faz um exame que **nenhuma guia do convênio cobre** (exame
particular, ou uma guia que faltou emitir), o laudo fica pronto no PRORADIS mas o
robô, corretamente, **não o fatura** — e isso hoje só aparece no log
(`[ANEX] EXAMES MISTOS — fora da guia`). Ninguém vê. Pode ser dinheiro na mesa
(guia esquecida) ou nada a fazer (particular) — mas o dono precisa **saber que
existe**. Caso real: PAULO SERGIO, tomografia cone beam (acc 40337054, 27/07),
pronta, sem guia.

## Detecção (o que é "sem guia") — precisão importa

"Sem guia" = laudo **pronto** cujo accession está em **`extras_acc`** (procedência:
não veio do relatório analítico do convênio → nenhuma guia o autoriza). Isto é
**diferente** de um laudo excluído por *tipo de exame* numa guia, que pode
pertencer a **outra guia** do mesmo paciente — esse NÃO é "sem guia".

- `excluidos` (nomes de arquivo dos laudos tirados da guia) já é calculado em
  `_filtrar_arquivos_da_gto` e gravado no item (`esteira.py:2226-2230`).
- `extras_acc` já está no item (`esteira.py:2224`).
- Regra: `laudos_sem_guia = [lp for lp in excluidos if _acc_do_laudo(lp) in extras_acc]`.
  Laudos excluídos são, por construção, **prontos** (só entram na pasta se
  baixaram); o filtro de procedência isola os **sem guia** (particulares).

## Arquitetura

**1. Esteira (aditivo, sem mudar decisão).** Logo após calcular `excluidos`
(esteira.py:2226), gravar `item["laudos_sem_guia"]` = lista de
`{"accession","exame","arquivo"}` dos excluídos por procedência. Helper testável
`_laudos_sem_guia(excluidos, extras_acc)`.

**2. Persistência (db.py, espelha `_sync_pendencias`).** Nova tabela
`avisos_sem_guia`: `id, conta, dia, gto, paciente, exame, accession, criado_em,
visto(bool), visto_por, visto_em`. Índice único **(conta, dia, accession)** →
re-run não duplica. `salvar_execucao` chama `_sync_avisos(s, conta, dia, exec_id,
avisos_info)` só em run REAL (dry_run não gera). Funções: `listar_avisos(status)`,
`contar_avisos_nao_vistos()`, `marcar_aviso_visto(id, quem)`,
`marcar_todos_avisos_vistos(quem)`. Migração via `create_all` + `_ensure_columns`.

**3. Backend (app.py).** `GET /avisos` (JSON: lista dos não-vistos, p/ o modal +
banner). `POST /avisos/<id>/ciente` e `POST /avisos/ciente-todos` → marca visto,
grava quem (session) + quando; devolve o novo total. Context processor injeta
`avisos_sem_guia` (contador) em toda página (como `pendencias_abertas`).

**4. Frontend (grita + persiste).** Num include compartilhado (`_topnav.html` ou
`_head.html`):
- **Banner** vermelho fixo no topo quando `avisos_sem_guia > 0`: "⚠ N exames
  prontos SEM GUIA — confirme que viu [Ver]". Fica em toda página até zerar.
- **Modal** ao carregar (dashboard/faturar): lista cada aviso (paciente · exame ·
  accession · dia) com **[Ciente]** por item + **[Ciente de todos]**. Buscado via
  `/avisos`. Fecha ao dar Ciente em tudo; reaparece enquanto houver não-visto.
- JS: `ciente(id)` / `cienteTodos()` → POST → atualiza banner+modal.

## Natureza (não é pendência)

Aviso ≠ pendência. Pendência = corrigir pra faturar (some ao faturar). Aviso =
confirmar que viu (some ao dar Ciente). Contadores e telas **separados** — não
polui a operação. Um exame particular legítimo é "ciente e pronto"; uma guia
esquecida vira ação de faturamento (do dono).

## Testes (TDD)

1. `_laudos_sem_guia`: exclui por procedência (accession ∈ extras_acc) e **ignora**
   o excluído que ESTÁ no convênio (é de outra guia) — caso PAULO (tomografia
   sem guia entra; um periapical de convênio não).
2. `_sync_avisos` upserta e **deduplica** por (conta, dia, accession) — dois runs
   do mesmo dia não duplicam o aviso.
3. `marcar_aviso_visto` grava visto=True + quem + quando; some da contagem.
4. `contar_avisos_nao_vistos` conta só os não-vistos.
5. Rotas: `/avisos/<id>/ciente` baixa o contador; `/avisos/ciente-todos` zera.

## Fora de escopo

- Não decide se é particular ou guia-esquecida (só avisa; o dono julga).
- Não fatura nada.
- A notificação por e-mail de falha técnica (spec própria, parada).
