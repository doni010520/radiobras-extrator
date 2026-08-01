# Fallback de identidade quando o nome do paciente está ilegível

**Data:** 2026-08-01
**Escopo:** um projeto isolado. Não inclui o cron D-4 nem a tela de Pendências (specs próprios).

## Problema

Solicitações de exame manuscritas têm o nome do paciente lido pelo Gemini com
frequência errado — pior ainda em nome de criança (ortodontia). Quando nenhum
anexo do prontuário lê um nome compatível com o da guia, o sistema reprova com
`PACIENTE_INCOMPATIVEL` ("nenhum documento do prontuário está no nome deste
paciente"), mesmo o pedido sendo do paciente certo.

Casos reais (28/07 Centro, reproduzidos ao vivo):

- **MAYSA ANTONIA COELHO JESUS DOS SANTOS** (GTO 195516236, nascida 2018): o
  pedido manuscrito lê o nome como "Maja"/"Maysa" (1 token) e o dentista como
  "Amanda Queiroz" ou "Mylena Queiroz" conforme a rodada — **a leitura é cara ou
  coroa**. A GTO da própria guia ESTÁ no prontuário dela (o campo 17 —
  "Mylena Silva Queiroz Santana" — foi lido dela). O único pedido dela é o
  manuscrito; não há pedido impresso alternativo.
- Padrão da unidade em 28/07: **8 de 29 guias** caíram nesse motivo, várias de
  uma mesma família ("Jesus dos Santos"), quase todas Doc Orto.

Diagnóstico: o prontuário é aberto pelo **cadastro exato** do paciente (nome +
código), então todo anexo ali já é, por construção, deste paciente. A checagem
do nome no papel é só uma trava contra documento mal-arquivado. Quando a letra é
ilegível, a identidade deve ser confirmada por **sinais que não dependem da
leitura frágil**.

## O que muda

Duas mudanças cirúrgicas, ambas na escolha da solicitação
(`esteira.py::_escolher_solicitacao` e `_reler_nao_classificados`). **Nada** de
download, anexação ou trava de duplicidade é tocado.

### Parte (3) — reclassificação também no "nome não bate"

Hoje a releitura que promove `documento → solicitação`
(`_reler_nao_classificados`, já existente e testada) só dispara quando o motivo
é `NAO_COBRE`. Passa a disparar **também** quando o resultado seria
`PACIENTE_INCOMPATIVEL`. Efeito: um pedido **impresso** que lê o nome certo, mas
foi classificado como "documento", ganha a chance de virar a solicitação
reconhecida — driblando a letra. (Não resolve a MAYSA, que só tem pedido
manuscrito, mas resolve os casos onde existe uma versão digitada.)

### Parte (1) — corroboração do prontuário (nível "Equilibrado")

Novo caminho de identidade, **apenas** para nome ilegível (`_nome_ausente`).
Aceita o candidato se as três condições valerem:

- **(a) Prontuário confirmado como sendo do paciente.** Verdadeiro se QUALQUER
  um:
  - a GTO **desta** guia está no prontuário (`_gtos_desta > 0` — o número da guia
    confere no PDF), OU
  - algum outro anexo do prontuário lê um nome compatível com a guia
    (`_nomes_compat` verdadeiro para alguma leitura).
- **(b) O dentista NÃO contradiz.** Rejeita só se o pedido lê um dentista
  claramente OUTRO — nome legível com ≥2 tokens significativos e **zero** em
  comum com o campo 17, e sem CRO batendo. Leitura parcial, sobrenome em comum
  ("Queiroz") ou dentista ilegível **não** contradizem (passam).
- **(c) Cobertura de exames.** Continua sendo decidida pela lógica de cobertura
  existente (o candidato entra na disputa; se não cobrir, vira `NAO_COBRE`, não
  fatura). Não é parte da checagem de identidade.

Diferença para o 2º sinal que já existe: o `_dentista_confere` exige o dentista
**bater** (≥2 tokens) — frágil na letra manuscrita. A corroboração exige que o
dentista **não contradiga** — robusto. A força vem de (a), não da leitura.

## Invariante de segurança

**Nunca anexar o pedido de um irmão na guia do outro.** Cada criança tem seu
próprio prontuário (código distinto); a corroboração exige o prontuário
confirmado como sendo do paciente da guia — a GTO da própria guia arquivada nele,
ou outro documento legível no nome dele. Um pedido de irmão só entraria se
mal-arquivado no prontuário confirmado E não contradissesse o dentista E cobrisse
— convergência muito improvável, e (b) barra o caso do irmão com dentista
diferente legível.

## Onde no código

- `esteira.py::_escolher_solicitacao` — recebe um novo parâmetro
  `prontuario_confirmado` (bool), calculado em `_decidir`. Adiciona o ramo de
  corroboração dentro do `elif _nome_ausente(...)` existente.
- `esteira.py::_decidir` — calcula `prontuario_confirmado` (`_gtos_desta > 0` ou
  qualquer leitura com nome compatível) e passa para `_escolher_solicitacao`;
  chama `_reler_nao_classificados` também no ramo `PACIENTE_INCOMPATIVEL`.
- Novo helper `_dentista_contradiz(a, dentista_gto, gto_txt) -> bool` (ou
  estender `_dentista_confere` para devolver 3 estados: confirma / neutro /
  contradiz).

## Testes (TDD — escritos antes)

Casos reais viram testes em `test_decisao.py`:

1. **MAYSA passa** — nome "Maja" (ilegível) + prontuário confirmado (GTO da guia
   presente) + dentista "Amanda Queiroz" (não contradiz "Mylena … Queiroz …",
   sobrenome em comum) + exames cobrem → aceita.
2. **Irmão trocado FALHA** — pedido no nome/dentista de um irmão (dentista
   legível DIFERENTE) numa guia de outro → rejeitado (b contradiz).
3. **Sem corroboração FALHA** — nome ilegível, prontuário NÃO confirmado (nenhuma
   GTO desta guia, nenhum nome compatível) → rejeitado (a falha) → continua indo
   para revisão como hoje.
4. **Reclassificação no "nome não bate"** — pedido impresso classificado como
   "documento" com nome compatível é relido e vira solicitação.
5. **Matriz de identidade preservada** — todos os testes atuais de
   `_nomes_compat`/parente/grafia/inicial continuam verdes (nada afrouxado no
   caminho do nome legível).

## Fora de escopo

- Não resolve a **Maria Clara** sozinho: nela o *exame* também sai ilegível
  ("perigeed"), então mesmo passando a identidade, a cobertura falha. É outro
  problema (leitura de exame), tratado à parte se o dono quiser.
- Não mexe em cron, tela de pendências, download nem anexação.
