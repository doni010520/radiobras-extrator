# RadioBras — Decisões, Arquitetura e Estado (doc vivo)

> Fonte única de verdade das **decisões duras** e do **estado** do sistema de
> faturamento RadioBras/OdontoPrev. Atualizado em 12/08/2026. Leia isto ANTES de
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
- 3 **Nós** (EVELYN=gemini 503 transitório → re-run recupera; ALESSANDRA=homônimo → nascimento; JOCASTA=docs no nome do responsável → CPF/identidade).
- 2 **Conferência** (ilegível).

---

## 6. Plano (roadmap acordado)

**Objetivo:** o app resolve o NOSSO sozinho em produção; só o EXTERNO vira pendência.
Tudo com LLM lendo e **código decidindo** (princípios 1–5).

- **[Fase 0] Taxonomia de retry** (`nosso_transitorio`/`nosso_logica`/`externo`) —
  lógica pura, TDD, e mostrar o selo nas pendências. Zero risco, alicerce.
- **[Fase 1] Identidade determinística em TODOS os sites de match:** wire do
  desempate por `nascimento` (já disponível no OdontoPrev via `dataNascimento`) e
  `carteirinha` (na guia) no site que falta (conserta ALESSANDRA, determinístico,
  zero risco). **Verificar se CPF está disponível** (pedido/cadastro) — chave que
  resolve o caso da mãe (JOCASTA).
- **[Fase 2] Extração LLM das chaves fortes:** o Gemini passa a extrair CPF/
  nascimento/carteirinha de cada doc (LEITURA), pro código casar por identidade.
- **[Fase 3] Loop de retry do transitório:** tabela `(gto,conta,dia,classe,
  tentativas,proximo_em,ultimo_erro)` + filtro `apenas_gtos` na esteira (retry
  DIRECIONADO, não re-rodar o dia todo) + worker no scheduler. Teto sugerido: 6
  tentativas, backoff 15m→30m→1h→2h→4h→8h, limitado ao prazo de 7 dias; estourou →
  pendência "nossa, não recuperou".
- **[Aberto] Proxy FlameProxies (produção):** precisa da URL atual (ou resultado do
  `/portal/testar`) pra diagnosticar (saldo? sessão expirada? credencial?). Sem
  proxy vivo, o usuário não roda pela tela do VPS.
- **[Aberto] NILSON** (ver seção 4).
- **[Aberto] Dúvida de domínio:** documentação ortodôntica (no pedido) cobre o
  periapical/interproximal que a guia autoriza à parte? (decide 3 pendências "não cobre").

---

## 7. Glossário rápido

- **Entregável:** folha JPEG com a logo verde RadioBras + cabeçalho + imagens
  (composta). É o que a esteira anexa. Radiografia crua (sem logo) NUNCA é anexada.
- **Traçado / CEPH:** laudo cefalométrico da telerradiografia (`_CEPH.pdf`).
- **GTO:** guia do OdontoPrev. **Accession:** id do exame no PRORADIS.
- **Contas/planos:** 388336 = Centro/Itaigara/Lauro/Periperi; 397950 = Tancredo;
  410923 = Camaçari.
- **Dois sites de match:** worklist/analítico (site-a) e prontuário (site-b).
