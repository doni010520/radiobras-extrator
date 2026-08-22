# RadioBras — Decisões, Arquitetura e Estado (doc vivo)

> Fonte única de verdade das **decisões duras** e do **estado** do sistema de
> faturamento RadioBras/OdontoPrev. Atualizado em 13/08/2026. Leia isto ANTES de
> propor mudanças — várias decisões abaixo já foram tomadas e não devem ser
> re-perguntadas.

---

## 1. Princípios duros (regras que NÃO mudam)

1. **LLM = olhos; decisão = determinística.** A LLM (Gemini) **só LÊ/EXTRAI** dados
   dos documentos (nome, exames, CPF, nascimento, carteirinha, dentista). **Quem
   DECIDE é o código, com regra determinística.** NUNCA decisão por LLM (nada de
   "a IA julga o caso ambíguo").

2. **A APLICAÇÃO decide, em produção, sozinha.** O objetivo é o app (cron/VPS)
   resolver autonomamente quando roda — não o Claude decidir caso a caso numa
   sessão. Claude **constrói a lógica**; a aplicação **executa e decide**.

3. **Casar por IDENTIDADE, não por NOME.** Nome é ambíguo (homônimo) e errado pra
   menor (pedido no nome da mãe/responsável). Match por **chave forte**:
   `nascimento → carteirinha → CPF`. Nome é só fallback fraco.

4. **Trava de identidade (o que torna a autonomia SEGURA):** o código **nunca
   anexa sem uma chave forte batendo**. É um `if` determinístico, não julgamento.
   Consequência: o pior caso possível é "segurou uma que dava" (pendência à toa,
   recuperável) — **NUNCA** "anexou o paciente errado" (glosa irreversível).

5. **O que é NOSSO, o sistema retenta sozinho até conseguir; o que é EXTERNO, vira
   pendência.** Três classes:
   - `nosso_transitorio` (gemini 5xx/timeout, rede/`net::`/tunnel/`context
     destroyed`, login flakou, JWT, throttle) → **retry automático** (bounded,
     backoff). Só isso é retry-recuperável.
   - `nosso_logica` (homônimo, nome não bate, ilegível, "revisão humana") → NÃO é
     retry cego (mesma entrada → mesmo erro). Precisa de **conserto determinístico
     por identidade** ou revisão. Meta: encolher isso via identidade.
   - `externo` (sem pedido/clínica, esperando laudo/radiologista, paciente não
     achado/cadastro, pedido não cobre) → **pendência**, sem retry.

6. **NUNCA disparar faturamento/anexação real por conta própria (Claude).** O DONO
   faz todos os faturamentos. Anexação é IRREVERSÍVEL (o portal não remove anexo).
   DRY é livre; REAL exige GO explícito do dono.

7. **Nunca afirmar "seguro/provado" sem verificar na fonte autoritativa.** Verificar
   ANTES de alarmar (API `/v1/gto/imagens`, imagem real). Lições: "ai vc me ferra"
   (Dossiê checava recordado, não landed) e os "72% sem imagem" (era falso — o
   entregável é composto; olhei a imagem antes de alarmar e evitei o erro).

8. **Imagem crua NUNCA é anexada — causa glosa.** Só entregável (folha com logo
   verde RadioBras). E o **entregável é COMPOSTO** (vários exames numa folha; ex.
   ALANA: panorâmica + 2 periapicais na mesma página). Portanto **NÃO dá pra
   auditar "imagem por exame/accession"** — o periapical "sem entregável próprio"
   está dentro do composto da panorâmica. (Gate de imagem por accession foi
   REVERTIDO em 12/08 por causa disso.)

9. **Relatório de faturamento = SEMPRE 3 listas em texto:** seria faturado / não
   seria / por quê (com nomes completos).

---

## 2. Estado atual do sistema (no ar)

- **App:** https://radiobras.benitechlab.com | repo `github.com/doni010520/radiobras-extrator` (branch master).
- **Deploy:** webhook `POST http://179.197.237.109:3000/api/deploy/bf377180da4acbd33280eaf292091f3fde6040b41b01021c` (autorizado disparar após push). VPS novo 179.197.237.109.
- **Local vs produção:** rodando LOCAL o OdontoPrev é DIRETO (IP residencial BR, aceito). No VPS o IP é datacenter → **BLOQUEADO**; produção depende do **proxy residencial BR (FlameProxies)** vivo (`ODONTO_PROXY_URL`). A instabilidade da sessão de 12/08 foi LOCAL (hidratação Vuetify + rede), NÃO o proxy.

### Corrigido e DEPLOYADO (correto)
- **Causa raiz "enviei ≠ grudou":** o anexador confirma por identidade (`_chave_anexo`) que CADA arquivo persistiu na guia antes de marcar OK (não confia no toast "sucesso"+contador). Só derruba o OK quando a leitura foi real.
- **Gate da TELE:** documentação ortodôntica não fatura sem o **laudo do traçado**. É por LAUDO (PDF `LAUDO_TELERRADIOGRAFIA_<acc>_CEPH`, nome por exame) — **não** é imagem/composto, então é seguro.
- **Nascimento com retry (Fase 1):** o `g["nascimento"]` (chave forte) passou a usar `_get_json_com_retry` (6 tentativas) — antes um rate-limit deixava nascimento="" e o desempate por homônimo morria (caso ALESSANDRA).
- **Taxonomia + loop de retry (Fases 0+3, DEPLOYADO 13/08):** `classe_retry(motivo)` → `transitorio|externo|logica`. Tabela `retry_fila` + `apenas_gtos` na esteira (retry DIRECIONADO, só as guias devidas) + worker `esteira.processar_retries` + scheduler `_retry_scheduler` (a cada 20min). **DESLIGADO por padrão** — liga com `RETRY_CRON=1` (mesmo padrão do `FATURAR_CRON`). Backoff 15m→8h, teto 6. Só `transitorio` retenta; anexação real com a mesma trava de identidade (idempotente, não duplica). Suite 241→249.

### REVERTIDO (estava errado)
- **Gate de imagem por accession** (commit revertido 12/08): entregável é composto → over-bloqueava. NÃO reintroduzir sem resolver o problema do composto.

### Descoberto (reutilizável)
- **API de upload do OdontoPrev:** `POST {_ODO_API}/v1/gto/upload?numeroFicha=<gto>&imagemGto=N`, multipart campo `multipartFile` = PDF (o nomeArquivo vira o nome na guia), header `Authorization: Bearer <token>` (mesmo token capturado no login que já uso pra ler). `imagemGto=N` = documento (não cópia da GTO). Muito mais confiável que o DOM (o widget "Arquivo digital" resiste à automação).
- `_ODO_API = https://gto-credenciado.odontoprev.com.br`. Leitura autoritativa: `GET /v1/gto/imagens?numeroFicha=<gto>` (nomeArquivo + flag `imagemGTO` "true"/"false").
- **Accession embutida no studyUID DICOM:** `1.2.640...9.<ACCESSION>.<n>` (penúltimo segmento).

---

## 3. Trabalho já entregue (não refazer)

- **30 documentações ortodônticas faturadas SEM o laudo da tele** (dias 08/07–06/08) → traçado re-anexado via API `/v1/gto/upload`, verificado 1-a-1 na API, **zero duplicata**. Encerrado com prova.
- **+26 faturamentos** em 07/08 e 08/08 (3 contas), rodando local, 0 falhou, 0 duplicata.
- **08/08 Camaçari:** PRORADIS sem laudos ainda (re-rodar quando laudar).

---

## 4. Achados e lições (pra não repetir)

- **Dois sites de match** que vivem desincronizados: (a) worklist/analítico
  (`listar_worklist_por_pacientes`) e (b) prontuário (`anexos_do_paciente`,
  `_cards_por_nascimento`). Correção de matching aplicada num, esquecida no outro —
  é a causa recorrente de bugs (ex.: desempate por nascimento existe no site-b,
  faltava no site-a → caso ALESSANDRA).
- **Entregável = folha composta** (ver princípio 8).
- **INGRID (caso ALANA):** "Ingrid ... Neves de Santana" era o **DENTISTA
  solicitante**, não a paciente. A paciente era ALANA SILVA VIANA (PERIPERI/388336),
  e ela estava **completa** (periapicais no composto). "Não foi as fotos" foi
  falso-alarme.
- **NILSON CONCEIÇÃO DOS REIS (31/07, gto 195651555, conta 388336):** guia com 3
  anexos e NENHUM laudo — falta o laudo da panorâmica (acc 40338421). Caso REAL,
  ainda aberto. Precisa GO pra re-anexar (se o laudo existir) ou cobrar.

---

## 5. Pendências 07/08+08/08 (guia única) — 40, não 66

Os "66" eram contagem por-rodada (com repetição). Únicas = **40**:
- 15 **Radiologista** (14 esperando laudo + 1 sem laudo/imagem) — externo, se resolve quando laudar.
- 10 **"Outros"** (bug do classificador): 6 = "revisão humana", 3 = "solicitação ilegível/pede exame diferente", 1 = CAUA ("data ajustada", suspeito).
- 6 **Clínica** (3 sem pedido c/ prontuário=0 = real; 3 "não cobre" = guia pede periapical/interproximal que o pedido de documentação não lista → **dúvida de domínio pendente**: doc ortodôntica cobre periapical?).
- 4 **Cadastro** (paciente não achado no PRORADIS).
- 3 **Nós** (EVELYN=gemini 503 transitório → re-run recupera; ALESSANDRA=homônimo → nascimento, JÁ CORRIGIDO Fase 1; ~~JOCASTA~~ **reclassificada** → ver abaixo).
- 2 **Conferência** (ilegível).

**JOCASTA (verificado 13/08 — NÃO era "nossa"):** a hipótese "docs no nome do responsável/CPF" estava **ERRADA**. Reprodução pelo registro salvo (`ExecucaoItem`): o prontuário da guia da JOCASTA (nasc. 1993-03-20) só tinha docs de **outras duas pessoas** — um pedido de "Lara da Costa" e um documento de "Maria de Fátima" (nasc. 01/01/2000). Nomes E nascimentos diferentes. **Não existe documento da JOCASTA lá.** Logo a recusa está **CORRETA** — anexar qualquer um seria glosa (paciente errado). É **externo/clínica** (falta a clínica anexar o pedido certo), não bug nosso. Correção feita: a mensagem da pendência deixou de sugerir "(mãe, responsável)" e "corrigir o nome no cadastro" (instrução perigosa que levaria a colar doc de terceiro) → agora manda **solicitar o pedido à clínica e nunca anexar doc de outra pessoa**.

---

## 6. Plano (roadmap acordado)

**Objetivo:** o app resolve o NOSSO sozinho em produção; só o EXTERNO vira pendência.
Tudo com LLM lendo e **código decidindo** (princípios 1–5).

- **[Fase 0 ✅ FEITA+DEPLOYADA 13/08] Taxonomia de retry** (`classe_retry` →
  `transitorio`/`logica`/`externo`) — lógica pura, TDD (`test_classe_retry`). Usada
  pelo hook em `salvar_execucao` pra alimentar a fila de retry.
- **[Fase 1 ✅ FEITA+DEPLOYADA 12/08] Identidade determinística nos sites de match.** Achados (12/08):
  - **CPF NÃO vem do OdontoPrev.** `/v1/gto/detalhada → beneficiario` só tem
    `codigo, codigoPlano, dataNascimento, isBradesco, nome, nomeEmpresa, plano`.
    Carteirinha NÃO é pesquisável no PRORADIS (testado 06/08). Logo a **chave forte
    é o NASCIMENTO** (que já temos, via `dataNascimento`). CPF só se a LLM ler do
    corpo do pedido (Fase 2) — pro caso da mãe (JOCASTA).
  - **Raiz REAL da ALESSANDRA (via reprodução, 12/08):** a disambiguação por
    nascimento **JÁ EXISTE** no prontuário (`extrair_anexos_dia`
    `_cards_por_nascimento`/`_card_wl_por_nome_nascimento`). O que falhava era o
    **INPUT**: o `g["nascimento"]` vinha de um GET ÚNICO SEM RETRY em
    `/v1/gto/detalhada` (`esteira.py:~2581`). Um rate-limit/queda (comum neste
    ambiente) deixava nascimento="" → nem encurtava a busca, nem desempatava → o
    homônimo morria. **É a classe TRANSITÓRIO disfarçada de "lógica".**
  - **FIX FEITO + DEPLOYADO (12/08):** o fetch do nascimento passou a usar
    `_get_json_com_retry` (6 tentativas, backoff). A chave forte chega confiável e o
    desempate existente acontece. Determinístico, sem LLM.
  - **Lição:** vários "nosso_logica" são, na verdade, TRANSITÓRIO intermitente
    (fetch/rede/Gemini). O **loop de retry (Fase 3) + hardening dos fetches** é o de
    maior alavanca — mais do que caçar cada site de match à mão.
- **[Fase 2 ✅ VERIFICADA 13/08 — veredito: NÃO construir matcher]** A ideia era "LLM
  extrai a identidade da criança do pedido → casa por identidade" (caso JOCASTA).
  **A verificação matou a premissa:** JOCASTA não é caso de mãe/menor — os docs no
  prontuário são de **outras pessoas** (nomes E nascimentos diferentes; ver seção 5).
  Forçar um match ali **causaria glosa** — seria o erro do gate de imagem de novo. O
  correto é o que o sistema **já faz**: recusar e virar pendência. Entregue: (a)
  mensagem da pendência tornada honesta/segura ("nunca anexar doc de terceiro;
  solicitar o pedido à clínica"), (b) confirmação de que a trava por nome já barra
  esses casos, (c) o loop (Fase 3) corretamente NÃO retenta essa classe (`logica`).
  **Chave forte extra (CPF/carteirinha lidos do pedido) fica como melhoria FUTURA
  opcional** — só ajudaria o caso genuíno de menor (pedido nomeia a criança), que já
  casa por `paciente_lido`; não vale o risco de mexer no prompt sem um caso real que
  precise disso.
- **[Fase 3 ✅ FEITA+DEPLOYADA 13/08] Loop de retry do transitório:** tabela
  `retry_fila (gto,conta,dia,classe,tentativas,proximo_em,ultimo_erro,resolvido)` +
  filtro `apenas_gtos` na esteira (retry DIRECIONADO) + worker `processar_retries` +
  scheduler `_retry_scheduler` (20min, `RETRY_CRON=1` liga). Backoff
  15m→30m→1h→2h→4h→8h, teto 6; estourou → fica pendência "nossa, não recuperou".
  Hook em `salvar_execucao`: faturado → `resolver_retry`; falha `transitorio` →
  `registrar_retry`. TDD `test_retry_loop`.
- **[EM TESTE 13/08] Proxy FlameProxies (produção):** RESOLVIDO no diagnóstico e em
  teste de durabilidade. O "a sessão morre no dia seguinte" era: (a) país podia vir
  Random → IP não-BR bloqueado pelo OdontoPrev; (b) sessão sticky FIXA expira em
  `time-N` min. **Fix (sem código — o rotador já existia):** gerar a proxy com
  **country-br + city-salvador + Sticky + session-<id>-time-60**, formato
  `http://user:pass@host:porta`. O `_fresh_sessid` (extrator_odontoprev.py:48) **já
  troca o `-session-<id>` a cada run** (confirmado: rotaciona só o token, preserva
  country/city/time) → cada execução pega uma sessão BR nova de 60 min → nunca
  reusa a de ontem. **Login ao vivo pelo proxy = OK (13/08)** (chegou em
  `credenciado.odontoprev.com.br/portal`). Conta FlameProxies: saldo $7.50, plano
  Residential 5GB ativo (1.06GB usados). Host `proxy.flameproxies.com:8989`, usuário
  `flma75147f1-...-session-<id>-time-60` (senha NÃO fica no git — vai só no env do
  EasyPanel). **AÇÃO:** setar `ODONTO_PROXY_URL` no EasyPanel e **observar quantos
  dias dura** (a hipótese é: com o rotador, dura indefinidamente).
- **[Aberto] NILSON** (ver seção 4).
- **[✅ RESOLVIDA 13/08] Dúvida de domínio — documentação ortodôntica × periapical:**
  o dono decidiu: **doc ortodôntica NÃO cobre periapical/interproximal — são exames
  DIFERENTES.** Um pedido só de documentação **não autoriza** uma guia de periapical.
  Consequência: as pendências "não cobre" (guia pede periapical, pedido só tem doc)
  estão **CORRETAS** → externo/Clínica (pedir à clínica um pedido que inclua o
  periapical, ou o periapical vai em guia própria com pedido próprio). **O código já
  faz certo** nessa direção: `expande_documentacao` nunca promove um pedido de doc a
  periapical (testes `test_decisao.py:261/267`) — nenhuma mudança necessária.
  - **Ponto adjacente a vigiar (NÃO mexido):** `_DOC_COMPONENTES` inclui `periapical`
    em `solicitacao_utils.py`, mas na direção OPOSTA — `componentes_da_documentacao`
    (esteira.py:1353) — que serve pra **não descartar laudos do entregável composto**
    (a folha da doc pode ter periapical dentro; caso ALANA/LOARA). É billing≠entregável:
    a decisão acima é de AUTORIZAÇÃO; essa é de reconhecimento de arquivo. Não rebati
    por conta própria (lição do gate de imagem). Se o dono quiser, avaliar se algum
    caminho faz um laudo de periapical "pegar carona" numa guia de doc.

- **[✅ FEITA 22/08 — NÃO DEPLOYADA] Falha de sistema sai do painel, vira try again
  e avisa o dono:** regra do dono — *"toda pendência que for de sistema deve ser
  resolvida por nós, fazendo uma nova tentativa; se é falha técnica não deve ser
  informada no painel da RadioBras, deve ser notificada para mim via WhatsApp e
  resolvida pelo próprio sistema"*.
  - **`db.eh_nosso(motivo, categoria)`** vira o gate único: nossa = transitório OU
    responsável `Nós` OU `categoria=='erro'` (fecha o furo do 429/`outros` apontado
    no desenho de 02/08). **Conferência NÃO é nossa** — ali falta olho humano no
    documento, e esconder seria sumir com trabalho real da operação.
  - **Painel:** `eh_pendencia_front` passou a ser `not eh_nosso(...)`. A falha nossa
    não volta pro operador **nem depois de esgotar o retry** (antes voltava como
    "Investigar"). Fila técnica do `/relatorios/pendencias` virou **só-admin** e saiu
    do **.xlsx** (aquele arquivo é entregue à clínica).
  - **Retry:** `deve_entrar_no_retry` = tudo que é nosso, teto 6 (decisão do dono:
    igual ao transitório). `nome_nao_bate`, `guia_ilegivel` e `anexacao` entraram no
    loop — a auditoria de 17/08 provou que boa parte era 503 intermitente disfarçado.
    O retry **re-lê**; não afrouxa a trava de identidade (JOCASTA segue recusa certa).
  - **Aborto de execução entrou na fila** (furo mais caro, aberto desde sempre):
    `salvar_execucao_falha` agora enfileira o DIA INTEIRO com a sentinela
    `__DIA__<conta>__<dia>` e avisa na hora. `processar_retries` roda o dia todo
    (`apenas_gtos=None`) quando vê a sentinela.
  - **Canal:** `notificador.py` (uazapi, `POST /send/text`). Três mensagens: aborto
    **na hora**, resumo **por rodada** (só das guias novas na fila — retry que falha
    de novo não re-avisa) e escalação quando **esgota o teto**. Falha quieto, nunca
    derruba faturamento. Teste em `/alerta/testar-whatsapp` (admin).
  - **Efeito medido em produção (22/08):** das **45** pendências abertas, **18 (40%)**
    são nossas e saem do painel — 13 `anexacao`, 3 `nome_nao_bate`, 2 `falha_tecnica`.
    Sobram **27** verdadeiras (Clínica 11, Cadastro 6, Radiologista 5, Conferência 5).
  - **PENDENTE p/ ligar:** instância uazapi conectada (a `GINES teste` está
    *disconnected* desde 20/08, `401 logged out`) + envs no EasyPanel. Suite: 301.

---

## 7. Glossário rápido

- **Entregável:** folha JPEG com a logo verde RadioBras + cabeçalho + imagens
  (composta). É o que a esteira anexa. Radiografia crua (sem logo) NUNCA é anexada.
- **Traçado / CEPH:** laudo cefalométrico da telerradiografia (`_CEPH.pdf`).
- **GTO:** guia do OdontoPrev. **Accession:** id do exame no PRORADIS.
- **Contas/planos:** 388336 = Centro/Itaigara/Lauro/Periperi; 397950 = Tancredo;
  410923 = Camaçari.
- **Dois sites de match:** worklist/analítico (site-a) e prontuário (site-b).
