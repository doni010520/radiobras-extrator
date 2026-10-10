# Fase 2 — Remover o legado (Plano de Implementação)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Tirar do sistema os fluxos, rotas, telas e scripts que ninguém usa mais (alguns perigosos: um terceiro caminho que anexa), sem mudar nenhuma decisão de faturamento, e deixar um repositório e um `.env` únicos.

**Architecture:** Remoção guiada por evidência (inventário de 10/10: alcance de imports a partir de `app`, rotas × templates, tabela `runs` parada desde 22/06). Cada tarefa termina com três provas: suíte (`exit 0`), rede de repetição (`replay_comparar.py` → `IGUAL`) e teste de fumaça das páginas do menu.

**Tech Stack:** Flask, pytest, rede de replay da Fase 1.

## Global Constraints

- Worktree `C:\Users\adoni\rb-prod`, branch `fix/modelo-rotulo-portal` (= master de produção).
- **Não apagar dado:** as tabelas `runs` e `run_itens` FICAM no banco. Só sai o código que as usa.
- **Não apagar pasta do dono sem perguntar** (cópia da OneDrive, clones antigos): só relatar.
- Commit só com `pytest` `exit=0` conferido (o `tail` mascara o código de saída).
- `env -u GEMINI_API_KEY python3 replay_comparar.py` tem que dar `IGUAL` antes de cada commit que toca `esteira.py`/`extrator_*`/`extrair_*`/`solicitacao_utils.py`.
- Commits terminam com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Inventário (evidência, 10/10)

| Peça | Situação | Evidência |
|---|---|---|
| `/fechar`, `/fechar/status`, `/fechar-simples`, `fechar.html`, `_run_fechar_job`, modal "FECHAR DIA" do dashboard (só abre por `/?run=`), botão "Faturar dia" de `plano_detalhe.html` | pipeline antigo que ANEXA, sem os consertos de 2026-08/10 | tabela `runs` parada em 22/06 |
| `fechar_dia.py` | só `_prefixo_casa` e `_ja_anexado_por_nos` são usados (pela esteira) | `grep` |
| `/relatorio` (`index.html`), `/gerar`, `/baixar_dia(+status/resultado)`, `/ciclo_dia(+status)`, `_run_job`, `_run_ciclo_job`, `ciclo_completo.py`, `extrator_arquivos.processar_dia` | extrator antigo (ZIP), fora do menu | só `index.html` chama |
| `/gtos` + `/api/gtos`, `/api/dashboard`, `/relatorio/run/<id>(.pdf)`, `relatorio_run.html`, funções `Run` de `db.py` | leem só `runs` (junho) | `/gtos` está no MENU mostrando junho |
| `teste_gemini.py`, `teste_imagem.py` | rascunho de dev | ninguém importa |

---

### Task 1: Teste de fumaça das páginas do menu
- Create `test_paginas_smoke.py`: cliente Flask sem o `before_request` de login; GET em `/`, `/faturar`, `/pendencias`, `/desfecho`, `/relatorios/dia`, `/relatorios/pendencias`, `/glosas`, `/relatorios`, `/usuarios`, `/tecnico`, `/portal`, `/relatorios/execucoes`; status < 500; e falha se alguma página do menu (`_topnav.html`) apontar para rota inexistente.
- Verificar: `pytest test_paginas_smoke.py` passa no código atual. Commit.

### Task 2: Fluxo antigo de anexação
- Mover `_prefixo_casa` e `_ja_anexado_por_nos` de `fechar_dia.py` para `esteira.py` (mesmo corpo), trocar o import.
- Remover de `app.py`: import de `fechar_dia`, `_run_fechar_job`, rotas `/fechar`, `/fechar/status/<id>`, `/fechar-simples`. Remover `templates/fechar.html`, o modal/JS de execução do `dashboard.html` (`abrirRun`, `executar`, `poll`, `pendingRun`), e trocar o link de `plano_detalhe.html` para `/faturar?plano={{ slug }}`; `faturar.html` pré-seleciona o plano vindo de `?plano=`.
- `planos.py`: handler do odontoprev passa a `"esteira"`. Atualizar `test_hapvida_isolamento.py` (os testes do `/fechar` saem; o de registro passa a esperar `"esteira"`).
- Apagar `fechar_dia.py`.
- Provas: suíte, replay IGUAL, fumaça. Commit.

### Task 3: Extrator antigo
- Remover de `app.py`: import de `processar_dia` e `ciclo_dia`, `_run_job`, `_run_ciclo_job`, rotas `/relatorio`, `/gerar`, `/baixar_dia*`, `/ciclo_dia*`. Remover `templates/index.html`, `ciclo_completo.py` e `extrator_arquivos.processar_dia` (se nada mais o usar).
- Provas. Commit.

### Task 4: Páginas e funções sobre a tabela `runs`
- Remover rotas `/gtos`, `/api/gtos`, `/api/dashboard`, `/relatorio/run/<id>`, `/relatorio/run/<id>.pdf`, `templates/gtos.html`, `templates/relatorio_run.html`, item "GTOs" do `_topnav.html`, a limpeza `limpar_runs_travadas` no startup e no scheduler.
- Remover de `db.py` as funções que só servem a essas rotas (conferir cada uma com `grep` antes). Modelos `Run`/`RunItem` FICAM (tabelas existem; `_melhores_runs_periodo` do gráfico ainda lê o histórico).
- Provas. Commit.

### Task 5: Scripts soltos
- Apagar `teste_gemini.py`, `teste_imagem.py`. Provas. Commit.

### Task 6: Repositório e `.env` únicos
- `C:\Users\adoni\radiobras-extrator`: `git checkout master && git pull --ff-only` (passa a ser a cópia de trabalho atual).
- `.env` local único nessa pasta, a partir do da OneDrive, sem `ODONTO_PROXY_URL` (a máquina do dono acessa direto) e com a nota de que produção usa o env do EasyPanel.
- Relatar ao dono as cópias obsoletas (OneDrive) para ele decidir apagar.

### Deploy
- Depois das Tasks 2–5, com `/api/diag` sem esteira ativa: push + webhook + conferir `/healthz` cair e voltar.
