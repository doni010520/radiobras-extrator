"""
esteira.py — Pipeline PARALELO de 3 estágios (descoberta -> download -> leitura).
NÃO anexa. Cada estágio tem fila + pool próprios, então rodam sobrepostos.

  DESCOBERTA  (N sessões OdontoPrev): abre cada GTO alvo, conta anexos, pendente
              -> fila_pend.
  DOWNLOAD    (M sessões PRORADIS, sessão compartilhada): baixa laudo+imagens
              (rápido, ~13s) e ENTREGA pra fila_leit (não fica preso na leitura).
  LEITURA     (K sessões PRORADIS + Gemini 2.5 Flash): baixa anexos do prontuário
              e lê as solicitações via Gemini (I/O-bound; substitui o Tesseract).

A separação da leitura num pool próprio é o ponto: o download não trava na
leitura, e a leitura escala sozinha (limitada pela cota do Gemini, não pela CPU).

rodar_esteira(data, m_download, n_desc, k_leitura, log, gemini_key) -> resumo.
"""
import io
import os
import queue
import shutil
import tempfile
import threading
import time
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

import requests
from playwright.sync_api import sync_playwright

from config import CONVENIOS, SEGMENTOS, PLANOS
from extrator_pacientes_analitico import BASE_URL as BASE, get_credentials
from extrator_arquivos import (
    _login_playwright, _get_relatorio_analitico,
    listar_worklist_por_pacientes, _processar_paciente,
)
from extrator_odontoprev import (
    login_odonto, get_credentials_odonto, abrir_consultar_gtos,
    consultar_periodo, listar_gtos, abrir_gto, _anexos_nomes, _anexos_count,
    normaliza_nome, upload_arquivos, _odo_requests_proxies, ler_dados_gto,
)
from fechar_dia import _prefixo_casa, _ja_anexado_por_nos
from extrair_anexos_dia import anexos_do_paciente
from gto_utils import (is_gto_pdf, extrair_observacao, gto_e_desta_guia,
                       _BOILER_49)
from solicitacao_utils import (gto_exames, canon_exames, gto_dispensa_laudo,
                               gto_solicitante, gto_texto,
                               expande_documentacao, componentes_da_documentacao,
                               lista_amigavel)
import json
import re

try:
    import psutil
    _PROC = psutil.Process(os.getpid())
except Exception:
    psutil = None
    _PROC = None

_GEM_PROMPT = ("É uma solicitação/requisição de exames odontológicos? Se sim, responda em "
               "JSON {solicitacao:true, tipo:'digitada'|'manuscrita', legivel:bool, exames:[...]}. "
               "Se não, {solicitacao:false}. Responda só o JSON.")
# Modelo do Gemini. Estava hardcoded em 3 chamadas — trocar de modelo exigia
# editar codigo e arriscar deixar uma para tras.
_GEM_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
# TEMPERATURA 0 — a leitura precisa ser REPRODUTÍVEL. No default (1.0) o mesmo
# documento era transcrito diferente entre execuções: MARIA CLARA (195244399) foi
# reprovada num run e aprovada no seguinte, com os MESMOS 6 arquivos. Guia que
# fatura ou vira pendência por sorte é inaceitável num sistema determinístico.
#
# THINKING — o 2.5 Flash "pensa" por padrão e cobra isso na tarifa de SAÍDA, a mais
# cara. Medido na própria conta: prompt "responda só: ok" gastou 6 de entrada,
# 1 de saída e 16 de raciocínio. A tarefa aqui é TRANSCREVER o que está no papel
# (o prompt diz "NÃO interprete, NÃO deduza"), então raciocínio é desperdício.
# Ajustável por env sem deploy, caso a leitura piore.
_GEM_THINKING = int(os.environ.get("GEMINI_THINKING_BUDGET", "0"))
# Teto de SAIDA por chamada. Caso SOPHIA (31/07, GTO 195467577): o modelo entrou
# em loop transcrevendo um lote e gerou 200 MIL tokens de saida em 762s, com o
# JSON ainda truncado no fim — uma guia segurou a execucao por 12 minutos. A
# transcricao normal de um lote inteiro fica abaixo de 6k; 16k e folga larga.
# Com o teto, a degeneracao falha em ~1 min e cai no resgate um-a-um.
# ATENCAO: nos modelos 2.5 o thinking conta DENTRO deste teto — se subir
# GEMINI_THINKING_BUDGET, suba GEMINI_MAX_SAIDA junto.
_GEM_MAX_SAIDA = int(os.environ.get("GEMINI_MAX_SAIDA", "16384"))
_gem_tokens = {"in": 0, "out": 0, "chamadas": 0}
_gem_tokens_lock = threading.Lock()

# Erro que NÃO adianta repetir: crédito/cota acabou, chave inválida, sem permissão.
# Insistir nesses casos foi o que fez uma execução da usuária levar 20 MINUTOS em
# 28/07 — cada GTO tentava 3x, e cada tentativa reenviava até 15 documentos ao
# Gemini antes de levar o 429. 40 guias × 3 tentativas de upload = 20 minutos para
# terminar em nada, com 40 pendências falsas no fim.
_GEM_FATAL = re.compile(r"RESOURCE_EXHAUSTED|429|quota|prepayment|credit|"
                        r"PERMISSION_DENIED|UNAUTHENTICATED|API[_ ]?key", re.I)
_gem_estado = {"fatal": None}
_campos_anexo = {"visto": False}
_campos_evento = {"visto": False}   # diagnostico 1x dos campos de /v1/gto/eventos/ficha


def _gem_fatal(e) -> bool:
    """Marca a leitura como indisponível para o RESTO da execução. A partir daí
    nenhuma GTO chama o Gemini — falha na hora, com motivo claro, em vez de
    arrastar a execução inteira."""
    if not _GEM_FATAL.search(str(e)):
        return False
    with _gem_tokens_lock:
        if _gem_estado["fatal"] is None:
            _gem_estado["fatal"] = str(e)[:200]
    return True


def _gem_cfg():
    from google.genai import types
    return types.GenerateContentConfig(
        temperature=0,
        # JSON nativo + teto de saida: reduzem muito a chance de loop de
        # geracao e limitam o estrago quando ele acontece (caso SOPHIA 31/07)
        max_output_tokens=_GEM_MAX_SAIDA,
        response_mime_type="application/json",
        thinking_config=types.ThinkingConfig(thinking_budget=_GEM_THINKING),
    )


def _contar_tokens(r):
    """Acumula o consumo que a resposta JÁ traz e ninguém lia. Sem isto não havia
    como responder 'quanto custou faturar este dia' — nem perceber o crédito
    acabando antes de 137 guias falharem com 429 (28/07)."""
    u = getattr(r, "usage_metadata", None)
    if not u:
        return
    with _gem_tokens_lock:
        _gem_tokens["in"] += getattr(u, "prompt_token_count", 0) or 0
        _gem_tokens["out"] += (getattr(u, "candidates_token_count", 0) or 0) \
            + (getattr(u, "thoughts_token_count", 0) or 0)
        _gem_tokens["chamadas"] += 1


def _mem_mb():
    if not _PROC:
        return -1
    try:
        tot = _PROC.memory_info().rss
        for ch in _PROC.children(recursive=True):
            try:
                tot += ch.memory_info().rss
            except Exception:
                pass
        return tot / 1e6
    except Exception:
        return -1


_ODO_API = "https://gto-credenciado.odontoprev.com.br"

_STOP_NOME = {"DE", "DA", "DO", "DAS", "DOS", "E"}

def _dist_edicao(a: str, b: str, teto: int = 2) -> int:
    """Distância de edição (Levenshtein), com corte em `teto` — só precisamos saber
    se é ERRO DE GRAFIA, não a distância exata."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > teto:
        return teto + 1
    ant = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        atual = [i]
        for j, cb in enumerate(b, 1):
            atual.append(min(ant[j] + 1, atual[j - 1] + 1, ant[j - 1] + (ca != cb)))
        if min(atual) > teto:
            return teto + 1
        ant = atual
    return ant[-1]


def _erro_de_grafia(tok: str, candidatos) -> bool:
    """True se `tok` é o mesmo token de `candidatos` escrito com erro (leitura/OCR).
    Tolerância proporcional: 1 letra em nomes curtos, 2 em nomes longos."""
    teto = 1 if len(tok) <= 6 else 2
    return any(_dist_edicao(tok, c, teto) <= teto for c in candidatos)


def _casa_por_concatenacao(tok: str, outros: list) -> bool:
    """`tok` é dois tokens ADJACENTES do outro lado escritos juntos?
    'VERALUCIA' == 'VERA'+'LUCIA'. Caso VERALUCIA SOUSA DOS SANTOS (22/07 Camaçari):
    a guia traz o nome composto grudado, o pedido do dentista traz separado, e a
    comparação token a token nunca fecha — a distância de 'VERALUCIA' para 'VERA'
    é 3, e o teto de erro de grafia é 2.

    É seguro porque PRESERVA o nome: mesma sequência de letras, só sem o espaço.
    Não abre porta para OUTRA pessoa — 'PEDRO' nunca vira 'JOAO' por concatenação.
    Aceita 1 letra de diferença no todo (acento perdido, plural)."""
    for i in range(len(outros) - 1):
        junto = outros[i] + outros[i + 1]
        if junto == tok or _dist_edicao(junto, tok, 1) <= 1:
            return True
    return False


def _nomes_compat(lido: str, alvo: str) -> bool:
    """Casa o nome LIDO na solicitação com o nome-ALVO (da GTO) por TOKENS, não por
    substring (evita 'ANA' casar 'ANA PAULA'). Exige >=2 tokens significativos em
    comum (nome+sobrenome).

    A divergência tolerada é APENAS ERRO DE GRAFIA (IONICE/JONICE). Antes, um token
    podia ser COMPLETAMENTE diferente: 'PEDRO SILVA SANTOS' casava com 'JOAO SILVA
    SANTOS' (2 sobrenomes iguais bastavam) e a solicitação do IRMÃO era anexada.
    Agora o token divergente precisa ser o mesmo nome mal escrito."""
    ta = [t for t in normaliza_nome(lido).split() if t not in _STOP_NOME and len(t) > 1]
    tb = [t for t in normaliza_nome(alvo).split() if t not in _STOP_NOME and len(t) > 1]
    if not ta or not tb:
        return False
    sa, sb = set(ta), set(tb)
    comuns = sa & sb
    if not comuns:
        return False                     # nenhum token identico: nao e a pessoa
    if len(sa) <= len(sb):
        menor, maior, lista_maior = sa, sb, tb
    else:
        menor, maior, lista_maior = sb, sa, ta
    # PAREAMENTO POR GRAFIA (caso SOPHIA CARVALHO DO ROSARIO, GTO 195469193,
    # 31/07): o manuscrito foi lido 'Sophia Carvallo do Rosamo' — DOIS tokens
    # com typo pequeno contavam como 'divergentes' e o nome morria no exame de
    # '2 tokens identicos'. Cada token do menor sem par exato tenta casar com UM
    # token livre do maior por erro de grafia, com teto pela MAIOR das duas
    # palavras (ROSAMO/6 x ROSARIO/7 -> teto 2). A porta continua fechada para
    # parente: exige >=1 token IDENTICO (acima) e tokens realmente diferentes
    # (PEDRO/JOAO, TAINA/THAILAN) nao pareiam por grafia.
    livres = sorted(maior - comuns)
    pareados = set()
    for tok in sorted(menor - comuns):
        for cand in livres:
            _teto = 1 if max(len(tok), len(cand)) <= 6 else 2
            if _dist_edicao(tok, cand, _teto) <= _teto:
                livres.remove(cand)
                pareados.add(tok)
                break
    if len(comuns) + len(pareados) < 2:
        return False
    se_falta = menor - comuns - pareados
    if not se_falta:
        return True                      # menor totalmente contido no maior
    if len(se_falta) > 1:
        return False                     # 2+ tokens divergentes -> outra pessoa
    tok = next(iter(se_falta))
    if _erro_de_grafia(tok, livres):
        return True
    # INICIAL ABREVIADA — "Isabela Benini M. Tavares" e a mesma pessoa que
    # "ISABELA BENINI MEDINA TAVARES". O dentista abrevia o nome do meio; a guarda
    # contra o irmao (PEDRO SILVA SANTOS x JOAO SILVA SANTOS) lia esse "M." como
    # token divergente e reprovava a guia por "outra pessoa".
    # Seguro porque so relaxa DEPOIS de 2+ tokens baterem exatamente: no caso do
    # irmao os comuns sao 1 e a funcao ja rejeitou la em cima.
    # Caso ISABELA BENINI MEDINA TAVARES, GTO 195416813, 25/07.
    _ini = re.sub(r"[^A-Za-z]", "", tok).upper()
    if len(_ini) == 1 and any(str(f).upper().startswith(_ini) for f in livres):
        return True
    # NOME COMPOSTO grudado num sistema e separado no outro (VERALUCIA / VERA LUCIA).
    # Usa a lista ORDENADA: só concatena tokens que estão lado a lado no nome.
    return _casa_por_concatenacao(tok, lista_maior)


def alvo_cobertura(gto_ex_desta, exames_portal, gto_ex_uniao):
    """Conjunto de exames que a solicitação precisa COBRIR, por precedência:

      1) a GTO DESTA guia (número conferido no PDF, ou lida por imagem);
      2) os eventos DESTA ficha no portal (a operadora dizendo o que autorizou);
      3) gto_ex — a referência acumulada da própria guia (NÃO de outras).

    ATENÇÃO (mudou em 01/08 — caso MARIA CLARA): o 3º argumento NÃO é mais a união
    de todas as GTOs do prontuário. O caller (_decidir) só acumula em gto_ex os
    exames da GTO cujo NÚMERO confere (gto_e_desta_guia) — GTO de outra visita/ano
    do mesmo paciente NÃO entra. Unir GTOs de outras visitas cobrava exames que
    ESTA guia nunca pediu (uma doc-orto de 2025 fazia uma guia de periapical de
    2026 "exigir" documentação) e reprovava pedido correto. NÃO reintroduza a
    união cross-guia aqui nem no caller. Sem guia identificável, o alvo fica VAZIO
    de propósito → a guia vira pendência GTO_ILEGIVEL (honesto), em vez de herdar a
    exigência de outra guia."""
    return set(gto_ex_desta or ()) or set(exames_portal or ()) or set(gto_ex_uniao or ())


def _nome_ausente(lido: str, alvo: str = "") -> bool:
    """A leitura do nome FALHOU? Isso e ausencia de evidencia — diferente de
    evidencia CONTRARIA (o nome de outra pessoa).

    Letra de dentista ilegivel NAO volta vazia: volta como LIXO. Caso JOSETE DIAS
    DE SANTANA (24/07 Tancredo): a IA leu 'Foxtel Ques de sontora' — tres palavras,
    entao a versao anterior (que so olhava se o nome estava vazio) tratou como nome
    de OUTRA PESSOA e rejeitou.

    O que separa lixo de parente e o SOBRENOME: irmao, pai e mae compartilham pelo
    menos um. Leitura falhada nao compartilha nenhum.
        >=2 tokens em comum -> mesma pessoa   (aceita antes de chegar aqui)
        exatamente 1        -> PARENTE        -> continua rejeitando
        zero                -> leitura falhou -> cai no segundo sinal (CRO/carimbo)
    """
    toks = [t for t in normaliza_nome(lido or "").split()
            if t not in _STOP_NOME and len(t) > 1]
    if len(toks) < 2:
        return True                      # vazio ou quase: nao ha nome
    if not alvo:
        return False
    alvo_t = {t for t in normaliza_nome(alvo).split()
              if t not in _STOP_NOME and len(t) > 1}
    # zero em comum -> nao e parente; e leitura falhada
    return not (set(toks) & alvo_t)


def _erro_de_leitura_do_nome(lido: str, alvo: str) -> bool:
    """O nome lido é uma LEITURA RUIM do nome da guia (MESMA pessoa, sobrenome
    corrompido ou com lixo) — e NÃO um nome LIMPO de OUTRA pessoa (irmão)? Aceita
    quando: o PRIMEIRO token do lido corresponde a algum token da guia (exato ou
    grafia) OU é curto/lixo (não é um nome limpo diferente), e sobra no máximo UM
    token 'estranho e limpo' (len>=5 sem correspondência). Rejeita quando o
    primeiro nome é um nome limpo diferente (irmão), ou há 2+ tokens limpos
    estranhos (ex.: THAILAN CABRAL ALMEIDA). NÃO depende do nome inteiro bater —
    é o que separa 'mal lido' de 'outra pessoa'. Caso SIDNEY (27/07): 'Sidney
    Sortas auto' é a mesma pessoa; 'Santos' virou 'Sortas' e 'auto' é lixo."""
    la = [t for t in normaliza_nome(lido).split() if t not in _STOP_NOME and len(t) > 1]
    lb = [t for t in normaliza_nome(alvo).split() if t not in _STOP_NOME and len(t) > 1]
    if not la or not lb:
        return False

    def _casa(t):
        for u in lb:
            _teto = 1 if max(len(t), len(u)) <= 6 else 2
            if t == u or _dist_edicao(t, u, _teto) <= _teto:
                return True
        return False

    casados = [t for t in la if _casa(t)]
    if not casados:
        return False                       # nada corresponde: é ausência, não corrupção
    if la[0] not in casados:
        # 1º nome NÃO corresponde a nenhum token da guia -> outra pessoa, mesmo
        # com nome curto (ANA, EVA, ZÉ). O code review mostrou que exigir len>=4
        # aqui deixava a IRMÃ de nome curto passar (ANA SANTOS x SIDNEY SANTOS) —
        # reabrindo o furo do SALLES. Na dúvida (1º nome garbled), vai p/ revisão;
        # o SIDNEY real não precisa disto (o 1º nome dele JÁ casa).
        return False
    estranhos = [t for t in la if t not in casados and len(t) >= 5]
    return len(estranhos) <= 1


def _dentista_confere(a: dict, dentista_gto: str, gto_txt: str = "") -> str:
    """Segundo sinal quando o nome do paciente nao foi lido: o CARIMBO do dentista.
    Carimbo e IMPRESSO — le muito melhor que letra de medico. Compara com o campo 17
    da GTO ('Nome do Profissional Solicitante').
    Devolve "cro", "nome" ou "" (nao confere)."""
    if not (dentista_gto or gto_txt):
        return ""
    # CRO: e numero, e numero o OCR le bem — no caso JOSETE a IA errou o nome
    # inteiro e acertou o CRO (20489). Procura no TEXTO da guia (campos 18/19 do
    # TISS: Conselho Profissional e Numero no Conselho), nao so no campo 17.
    # Exige >=4 digitos e fronteira de palavra para nao casar dentro de outro numero.
    cro_g = re.sub(r"\D", "", str(a.get("cro_lido") or ""))
    if cro_g and len(cro_g) >= 4 and re.search(r"\b" + cro_g + r"\b", gto_txt or ""):
        return "cro"
    if not dentista_gto:
        return ""
    lidos = {t for t in normaliza_nome(a.get("dentista_lido") or "").split()
             if t not in _STOP_NOME and len(t) > 2}
    alvo = {t for t in normaliza_nome(dentista_gto).split()
            if t not in _STOP_NOME and len(t) > 2}
    return "nome" if len(lidos & alvo) >= 2 else ""


def _dentista_contradiz(a: dict, dentista_gto: str, gto_txt: str = "") -> bool:
    """True SÓ quando o pedido lê um dentista claramente OUTRO: nome legível com
    >=2 tokens significativos e ZERO em comum com o campo 17, e sem CRO batendo.
    Leitura parcial (1 token), sobrenome em comum (Amanda QUEIROZ x Mylena
    QUEIROZ) ou dentista ilegível NÃO contradizem.

    Diferente de _dentista_confere (que exige o dentista BATER, frágil na letra
    manuscrita): aqui só se pergunta se ele CONTRADIZ. É a trava do fallback de
    nome ilegível (caso MAYSA): a força vem da corroboração do prontuário, e o
    dentista serve só para barrar o pedido de OUTRO dentista (pedido de irmão)."""
    cro_g = re.sub(r"\D", "", str(a.get("cro_lido") or ""))
    if cro_g and len(cro_g) >= 4 and re.search(r"\b" + cro_g + r"\b", gto_txt or ""):
        return False                    # CRO bate -> confirma, nunca contradiz
    lidos = {t for t in normaliza_nome(a.get("dentista_lido") or "").split()
             if t not in _STOP_NOME and len(t) > 2}
    alvo = {t for t in normaliza_nome(dentista_gto or "").split()
            if t not in _STOP_NOME and len(t) > 2}
    if not alvo or len(lidos) < 2:
        return False                    # sem referência ou leitura parcial
    return len(lidos & alvo) == 0       # 2+ tokens legíveis, nenhum em comum


# Distancia maxima, em dias, para considerar que dois pedidos do prontuario sao do
# MESMO episodio de tratamento e portanto podem ser pareados com guias diferentes.
# Curta de proposito: os casos que o dono reprovou (MATHEUS, JAQUELINE, VANESSA)
# tinham pedido de 2023 para exame de 2026 — ~1000 dias, longe demais para passar.
_JANELA_PAREAMENTO_DIAS = int(os.environ.get("SOLIC_PAREAMENTO_DIAS", "30"))


def _escolher_solicitacao(leituras, nome_gto, gto_ex, n_cands, dentista_gto="",
                          detalhe=None, gto_txt="", prontuario_confirmado=False):
    """NÍVEL 2 — o CÓDIGO escolhe a solicitação certa entre as leituras que o Gemini
    transcreveu (uma por anexo). Determinístico: tipo solicitação, legível, paciente
    compatível (tokens) e exames que COBREM os da GTO. Desempate: mais exames em
    comum, depois o mais recente (idx menor). Retorna (idx, leitura, motivo_ou_None)."""
    melhor = None
    algum_pac = False
    cands_ok = []          # candidatas validas (tipo/legivel/paciente), em ordem
    # CORROBORAÇÃO só vale quando o pedido de nome ILEGÍVEL é ÚNICO. Com dois ou
    # mais (o do paciente + o de um irmão mal-arquivado, mesmo dentista da
    # família — que não contradiz), não dá para distinguir de quem é cada um →
    # vai para revisão, nunca chuta. Achado do code review (família de
    # ortodontia). O carimbo do dentista (_dentista_confere) NÃO é afetado: é
    # sinal mais forte e segue valendo mesmo com vários ilegíveis.
    # Ambíguo = pedido cujo nome NÃO casa e é (a) AUSENTE/ilegível OU (b) uma
    # LEITURA RUIM do próprio nome da guia (mesma pessoa, sobrenome corrompido —
    # caso SIDNEY). Nos dois casos a identidade depende da corroboração, então a
    # unicidade tem de contar os DOIS: dois ambíguos -> revisão (não chuta qual).
    def _ambiguo(_p):
        return (not _nomes_compat(_p, nome_gto)
                and (_nome_ausente(_p, nome_gto) or _erro_de_leitura_do_nome(_p, nome_gto)))
    _n_ambiguos = sum(
        1 for _a in (leituras or [])
        if isinstance(_a, dict) and _a.get("tipo") == "solicitacao"
        and bool(_a.get("legivel", True))
        and _ambiguo(_a.get("paciente_lido") or ""))
    _corrobora_ok = prontuario_confirmado and _n_ambiguos <= 1
    for a in leituras or []:
        if not isinstance(a, dict):
            continue
        ai = a.get("idx")
        if not (isinstance(ai, int) and 0 <= ai < n_cands):
            continue
        # CONSERVADOR: só é candidato o que o Gemini LEU positivamente como
        # "solicitacao". "outro"/"laudo"/null NÃO passam — evita anexar um laudo
        # mal-rotulado no lugar da solicitação (o código nunca fatura errado; na
        # dúvida vai pra revisão). Recomendação do revisor.
        if a.get("tipo") != "solicitacao":
            continue
        if not bool(a.get("legivel", True)):
            continue
        _lido = a.get("paciente_lido") or ""
        if _nomes_compat(_lido, nome_gto):
            a["_via"] = "nome"
        elif _nome_ausente(_lido, nome_gto):
            # NOME NAO LIDO — nao e prova contra. Aceita se houver OUTRO sinal: o
            # carimbo do dentista bate com o campo 17 da GTO. O carimbo e impresso,
            # entao le bem; e o documento ja veio do prontuario DESTE paciente.
            _via = _dentista_confere(a, dentista_gto, gto_txt)
            if _via:
                a["_via"] = "dentista_" + _via
            # CORROBORAÇÃO DO PRONTUÁRIO (caso MAYSA, 28/07): pedido de crianca
            # manuscrito, nome ilegivel E o dentista tambem sai instavel na letra
            # (as vezes bate, as vezes nao). Aceita SE: o prontuario esta
            # CONFIRMADO como sendo do paciente (a GTO da propria guia esta nele) E
            # este e o UNICO pedido de nome ilegivel (_corrobora_ok) E o dentista
            # NAO CONTRADIZ. A letra deixa de ser obrigatoria; a trava de familia
            # e a UNICIDADE (varios ilegiveis do mesmo dentista -> revisao), nao o
            # dentista (que na familia e o mesmo).
            elif _corrobora_ok and not _dentista_contradiz(a, dentista_gto, gto_txt):
                a["_via"] = "corroboracao"
            else:
                continue
        else:
            # Nome presente mas NÃO compatível. Antes: sempre rejeita (guarda do
            # irmão). NOVO (SIDNEY, 27/07): se o nome é uma LEITURA RUIM do nome
            # da guia (mesma pessoa, sobrenome corrompido) — e não um nome LIMPO
            # de OUTRA pessoa — aceita por corroboração (prontuário confirmado +
            # único ambíguo + dentista não contradiz). O nó não é "o nome bater";
            # é distinguir 'mal lido' de 'irmão'. Um nome limpo e diferente
            # (THAILAN CABRAL SALLES, ou primeiro nome diferente) continua barrado
            # dentro de _erro_de_leitura_do_nome.
            if (_corrobora_ok and _erro_de_leitura_do_nome(_lido, nome_gto)
                    and not _dentista_contradiz(a, dentista_gto, gto_txt)):
                a["_via"] = "nome_mal_lido"
            else:
                continue
        algum_pac = True
        # expande SÓ o lado da solicitação: quem pede os componentes (panorâmica +
        # telerradiografia + ...) está pedindo uma documentação. A recíproca não vale.
        ex = expande_documentacao(
            canon_exames(" ".join(str(e) for e in (a.get("exames_lidos") or [])),
                         recuperar=True))   # PEDIDO manuscrito: recupera exame mal lido
        # QUASE-ACERTO: candidato que passou em tipo/legivel/paciente mas falhou na
        # cobertura. A mensagem PRECISA descrever ESTE, com o MESMO conjunto que a
        # decisao usou (ja expandido). Antes ela recalculava por fora, pegando o
        # primeiro candidato com nome compativel e SEM expandir documentacao — e
        # entao dizia "FALTA no pedido: nenhum" numa guia reprovada por falta de
        # cobertura. Absurdo logico, 6 casos em 23-24/07.
        # A MAIS RECENTE VENCE — regra do dono (30/07): "nunca usar uma solicitacao
        # mais velha se houver uma mais nova". Antes a COBERTURA decidia primeiro e a
        # recencia era so desempate: quando a solicitacao nova nao cobria e uma
        # antiga cobria, a ANTIGA era escolhida — e depois tinha a data reescrita.
        # Medido: 10 guias faturadas com pedido de ate 1066 dias antes do exame
        # (MATHEUS 20/09/23 para exame de 17/07/26; JAQUELINE 22/08/23; VANESSA
        # 07/11/23). Um pedido de 2023 nao e o pedido de um exame de 2026.
        #
        # Recencia = ordem do anexo no prontuario (a lista chega ordenada por id
        # decrescente, entao idx MENOR = mais novo). A data escrita no papel entra
        # so como desempate, porque e lida por IA e as vezes falha.
        _dt = _parse_br_date(a.get("data_solicitacao"))
        cands_ok.append({"idx": ai, "a": a, "ex": ex, "data": _dt,
                         "cobre": bool(gto_ex and gto_ex.issubset(ex))})
    # ESCOLHA: a mais recente entre as candidatas — e SO ela e avaliada na cobertura.
    if cands_ok:
        cands_ok.sort(key=lambda c: (c["idx"], -(c["data"].toordinal() if c["data"] else 0)))
        recente = cands_ok[0]
        if isinstance(detalhe, dict):
            detalhe["escolhida_idx"] = recente["idx"]
            detalhe["outras"] = len(cands_ok) - 1
        if recente["cobre"]:
            if isinstance(detalhe, dict):
                detalhe["idxs"] = [recente["idx"]]
            return recente["idx"], recente["a"], None
        # UM PEDIDO PODE VIR EM MAIS DE UMA FOLHA. Caso JUCILENE PINHEIRO DE OLIVEIRA
        # (GTO 195371168, 24/07 Centro): a guia autoriza panoramica + periapical +
        # interproximal, e o prontuario tem DUAS solicitacoes da MESMA dentista, na
        # MESMA data — "RADIOGRAFIA PANORAMICA COM LAUDO" e "RADIOGRAFIA PERIAPICAL E
        # INTERPROXIMAL DAS UNIDADES 24, 25, 26, 27, 36 e 37". Juntas cobrem; sozinhas
        # nenhuma cobre. O sistema avaliava uma por vez e reprovava.
        #
        # "A mais recente vence" continua valendo: o que se soma e o pedido mais
        # recente, que pode estar escrito em varias folhas da MESMA DATA. Folha de
        # data anterior fica de fora, como o dono definiu.
        if recente["data"]:
            grupo = [c for c in cands_ok if c["data"] == recente["data"]]
            if len(grupo) > 1:
                uniao = set()
                for c in grupo:
                    uniao |= c["ex"]
                if gto_ex and gto_ex.issubset(uniao):
                    if isinstance(detalhe, dict):
                        detalhe["idxs"] = [c["idx"] for c in grupo]
                        detalhe["somadas"] = len(grupo)
                    return recente["idx"], recente["a"], None
                recente = {**recente, "ex": uniao}   # a mensagem fala do CONJUNTO
        # PAREAMENTO — o paciente pode ter MAIS DE UMA GUIA no mesmo episodio, e cada
        # uma tem o SEU pedido. Caso OZIEL FERRAZ SANTANA, 25/07: guia 195420152
        # (documentacao ortodontica) e guia 195420167 (periapical), com os dois pedidos
        # no mesmo prontuario. "A mais recente vence" entregava o pedido da doc orto
        # para as DUAS, e a guia de periapical virava pendencia com o proprio pedido
        # dela a um anexo de distancia.
        #
        # A recencia continua mandando — so entra aqui quem cobre a guia E esta dentro
        # da mesma janela do pedido mais recente. Isso preserva a regra do dono (nada
        # de pedido de 2023 para exame de 2026: la a distancia passava de 1000 dias) e
        # exige data LIDA nos dois. Sem data nao da para afirmar que e o mesmo
        # episodio, e no escuro a guia vai para uma pessoa, como sempre.
        if gto_ex and recente.get("data"):
            _pareado = next(
                (c for c in cands_ok[1:]
                 if c["cobre"] and c["data"]
                 and abs((c["data"] - recente["data"]).days) <= _JANELA_PAREAMENTO_DIAS),
                None)
            if _pareado:
                if isinstance(detalhe, dict):
                    detalhe["escolhida_idx"] = _pareado["idx"]
                    detalhe["idxs"] = [_pareado["idx"]]
                    detalhe["pareado"] = True
                return _pareado["idx"], _pareado["a"], None
        # A mais recente NAO cobre. NAO cai para uma anterior: se existe pedido novo,
        # e ele que vale. A guia vira pendencia dizendo o que falta NELE.
        if isinstance(detalhe, dict):
            detalhe.update({"idx": recente["idx"], "lidos": sorted(recente["ex"]),
                            "falta": gto_ex - recente["ex"]})
        melhor = None
    if not leituras:
        return None, None, "LEITURA_VAZIA"
    if not gto_ex:
        return None, None, "GTO_ILEGIVEL"
    if not algum_pac:
        return None, None, "PACIENTE_INCOMPATIVEL"
    return None, None, "NAO_COBRE"


# NÃO listar nomes de exame aqui. A versão anterior enumerava
# ("procure: panoramica, periapical, ...") e isso entrega o gabarito: num
# manuscrito em cursiva o modelo tende a "ver" o termo sugerido. Como a união das
# leituras só ACRESCENTA exame, uma alucinação vira faturamento — o lado errado da
# regra "na dúvida, pendência". Aqui ele só transcreve; quem reconhece o exame é o
# canon_exames() do código.
_RELEITURA_PROMPT = """Este anexo é um PEDIDO/SOLICITAÇÃO de exames odontológicos, possivelmente
MANUSCRITO (letra cursiva). Transcreva LITERALMENTE tudo que está escrito nos campos
de exames/procedimentos pedidos — palavra por palavra, como aparece no papel, mesmo que
esteja abreviado, com grafia imperfeita ou você não reconheça o termo.

O pedido costuma ter MAIS DE UMA SEÇÃO: uma lista numerada de exames radiográficos
(ex.: "EXAMES RADIOGRÁFICOS: 1-panorâmica, 2-telerradiografia") E, separadamente, um
bloco de "DOCUMENTAÇÃO", "FOTOS/FOTOGRAFIAS INTRA E EXTRA BUCAIS" ou "MODELOS".
Transcreva TODAS as seções pedidas — NÃO pare na primeira lista; se houver fotografias
ou modelos pedidos, inclua-os também.

Se for um FORMULÁRIO COM QUADRADINHOS/CAIXAS de opções pré-impressas, transcreva
APENAS os exames cuja caixa está MARCADA (X, tique, traço, círculo, rabisco);
IGNORE as opções em branco — são só o cardápio impresso, não o pedido.

NÃO interprete, NÃO complete, NÃO deduza e NÃO acrescente nada que não esteja escrito.
Se não conseguir ler um trecho, omita-o em vez de adivinhar.

Responda APENAS JSON (sem markdown): {"exames": ["...", "..."]}"""


_RELEITURA_TIPO_PROMPT = """Este e UM anexo de prontuario odontologico. Voce e um
LEITOR/transcritor: NAO decida nada, apenas transcreva o que esta escrito.

Responda APENAS JSON (sem markdown):
{"tipo": "solicitacao" | "laudo" | "documento" | "nota_fiscal" | "raio_x" | "outro",
 "legivel": true|false,
 "paciente_lido": "<nome do paciente escrito no anexo; \"\" se nao houver ou ilegivel>",
 "dentista_lido": "<nome no carimbo/assinatura; \"\" se nao houver>",
 "cro_lido": "<numero do CRO no carimbo, so digitos; \"\" se nao houver>",
 "exames_lidos": ["<cada exame PEDIDO, de TODAS as secoes do pedido: a lista numerada de exames radiograficos E os blocos de DOCUMENTACAO / FOTOS INTRA E EXTRA BUCAIS / MODELOS; em formulario de caixas, SO os MARCADOS (X/tique/circulo), ignore as opcoes em branco; em pedido a mao, os escritos>"],
 "data_solicitacao": "<DD/MM/AAAA escrita no anexo, ou null>"}

"solicitacao" e um PEDIDO/REQUISICAO de exames feito por um dentista — costuma
comecar com "Solicito", trazer o nome do paciente e a lista de exames, e ter
carimbo/assinatura. O pedido pode ter VARIAS SECOES (exames radiograficos numerados
E, separadamente, um bloco de DOCUMENTACAO/FOTOS/MODELOS) — transcreva TODAS, nao
pare na primeira lista. Transcreva LITERALMENTE; nao interprete, nao complete, nao
deduza. Se nao conseguir ler um trecho, omita em vez de adivinhar."""


_BOX_DATA_PROMPT = """Este anexo é um PEDIDO/SOLICITAÇÃO de exames odontológicos. Nele há uma
DATA escrita (a data em que o pedido foi feito), normalmente perto do nome da
cidade e da assinatura do dentista, no rodapé.

Sua ÚNICA tarefa: localizar essa data na imagem e devolver a caixa que a contém,
em coordenadas de 0 a 1000 (0 = topo/esquerda, 1000 = base/direita).

Responda APENAS JSON (sem markdown):
{"data_solicitacao": "<DD/MM/AAAA lida, ou null>",
 "box_data": [ymin, xmin, ymax, xmax]}

Se realmente não houver data escrita, retorne box_data: null."""


def _pdf_para_imagem(blob):
    """Renderiza a 1ª página de um PDF para PNG, para o ajuste de data (que edita
    IMAGEM com o PIL) também funcionar em solicitação-PDF. Devolve (png_bytes,
    'image/png') ou None se não for PDF/render falhar. A data (box_data) é
    normalizada 0-1000, então mapeia igual na imagem renderizada.
    Caso SIDNEY (27/07): solicitação em PDF caía em revisão por 'data vencida'."""
    try:
        import fitz  # PyMuPDF (já é dependência)
        doc = fitz.open(stream=blob, filetype="pdf")
        try:
            # SÓ 1 página: multi-página não pode virar 1 imagem — truncaria as
            # páginas 2+ num upload IRREVERSÍVEL (achado do code review). Multi e
            # 0-página -> None -> fluxo antigo (revisão p/ vencida, PDF p/ inserir).
            if doc.page_count != 1:
                return None
            pix = doc.load_page(0).get_pixmap(dpi=200)   # nítido p/ leitura, ainda leve
            png = pix.tobytes("png")
        finally:
            doc.close()
        return (png, "image/png") if png else None
    except Exception:
        return None


def _reler_box_data(gem, cands, idx):
    """Releitura FOCADA só da caixa da data de UM anexo.

    Caso ESTER SANTOS EISENBACH (GTO 195441738, 27/07): o pedido foi validado e
    a data está vencida (>60 dias), então o sistema precisa reescrever a data de
    hoje POR CIMA da antiga — e para isso precisa saber ONDE ela está (box_data).
    Na leitura em lote a IA leu a data mas não devolveu a caixa, e a guia virava
    pendência manual. Perguntar só a localização, num anexo só, acerta muito mais.
    Devolve (box_data, data_lida) ou (None, None)."""
    from google.genai import types
    if not (isinstance(idx, int) and 0 <= idx < len(cands)):
        return None, None
    try:
        _fn, _mime, _blob, _sv = cands[idx]
        r = gem.models.generate_content(
            model=_GEM_MODEL, config=_gem_cfg(),
            contents=[types.Part.from_bytes(data=_blob, mime_type=_mime),
                      _BOX_DATA_PROMPT])
        _contar_tokens(r)
        t = re.sub(r"^```json|^```|```$", "", (r.text or "").strip(), flags=re.M).strip()
        d = json.loads(t) or {}
        return _box4(d.get("box_data")), d.get("data_solicitacao")
    except Exception as e:
        if _gem_fatal(e):
            raise
        return None, None


def _reler_nao_classificados(gem, cands, leituras, max_reler=4, nome_gto=""):
    """2a passada, um anexo POR VEZ, nos que NAO foram classificados como
    solicitacao na leitura em lote.

    Por que existe: a 1a leitura manda ate 15 anexos juntos e o modelo erra a
    CLASSIFICACAO em documentos parecidos. Caso JUCILENE PINHEIRO DE OLIVEIRA
    (GTO 195371168): o prontuario tinha duas solicitacoes quase identicas — mesmo
    timbre, mesmo layout, mesmo carimbo, diferindo por uma linha de texto. So uma
    virou candidata; a outra (a da panoramica) foi descartada, e a guia reprovou
    por "falta panoramica" com a panoramica na folha ao lado.

    Le UM documento com atencao, que e onde esta o ganho — a releitura focada de
    exames ja usava esse mesmo principio. Atualiza `leituras` in-place. Nao manda
    o nome nem os exames da guia: quem compara continua sendo o codigo."""
    from google.genai import types
    ja = {a.get("idx") for a in (leituras or []) if isinstance(a, dict)
          and a.get("tipo") == "solicitacao"}
    # PRIORIDADE: releitura tem custo (1 chamada/anexo) e teto (max_reler).
    # O sinal mais forte de "solicitacao mal classificada como documento" e o
    # anexo ja ter sido lido com o NOME do proprio paciente E com exames —
    # caso MARIA CLARA (GTO 195436162, 27/07): 10 anexos, e os 3 pedidos dela
    # (no nome dela, com exames) caiam DEPOIS dos documentos genericos e nunca
    # eram relidos (teto de 4). Ordena: (nome-compat + exames) > nome-compat > resto.
    _by_idx = {a.get("idx"): a for a in (leituras or []) if isinstance(a, dict)}

    def _prio(i):
        a = _by_idx.get(i) or {}
        _compat = bool(nome_gto) and _nomes_compat(a.get("paciente_lido") or "", nome_gto)
        _tem_ex = bool(a.get("exames_lidos"))
        if _compat and _tem_ex:
            return (0, i)
        if _compat:
            return (1, i)
        return (2, i)
    _nao = sorted((i for i in range(len(cands)) if i not in ja), key=_prio)
    alvos = _nao[:max_reler]
    novos = 0
    for i in alvos:
        try:
            _fn, _mime, _blob, _sv = cands[i]
            r = gem.models.generate_content(
                model=_GEM_MODEL, config=_gem_cfg(),
                contents=[types.Part.from_bytes(data=_blob, mime_type=_mime),
                          _RELEITURA_TIPO_PROMPT])
            _contar_tokens(r)
            t = re.sub(r"^```json|^```|```$", "", (r.text or "").strip(), flags=re.M).strip()
            d = json.loads(t) or {}
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("tipo") != "solicitacao":
            continue
        d["idx"] = i
        d["_releitura"] = True          # rastro: entrou na 2a passada
        # substitui a leitura anterior deste anexo (ou acrescenta)
        for k, a in enumerate(leituras or []):
            if isinstance(a, dict) and a.get("idx") == i:
                leituras[k] = d
                break
        else:
            leituras.append(d)
        novos += 1
    return novos


def _reler_exames_focado(gem, cands, leituras, nome_gto):
    """2ª leitura quando a 1ª não cobriu os exames da GTO: reenvia SÓ o candidato
    que falhou apenas na cobertura, isolado (o ganho vem de ler UM documento com
    atenção, não de sugerir termos). Atualiza exames_lidos IN-PLACE com a união e
    guarda a leitura original em exames_lidos_1a para auditoria.

    NÃO relê quando a 1ª passada não leu exame NENHUM: aí o documento é ilegível de
    fato, e insistir seria uma 2ª tentativa às cegas — vira pendência direto."""
    from google.genai import types
    for a in leituras or []:
        if not isinstance(a, dict):
            continue
        ai = a.get("idx")
        if not (isinstance(ai, int) and 0 <= ai < len(cands)):
            continue
        if a.get("tipo") != "solicitacao" or not bool(a.get("legivel", True)):
            continue
        if not _nomes_compat(a.get("paciente_lido") or "", nome_gto):
            continue
        _lidos1 = [str(e) for e in (a.get("exames_lidos") or [])]
        if not _lidos1:
            continue          # nada lido na 1ª -> não insiste
        try:
            fn2, mime2, blob2, _sv = cands[ai]
            r2 = gem.models.generate_content(
                model=_GEM_MODEL, config=_gem_cfg(),
                contents=[types.Part.from_bytes(data=blob2, mime_type=mime2),
                          _RELEITURA_PROMPT])
            _contar_tokens(r2)
            t2 = re.sub(r"^```json|^```|```$", "", (r2.text or "").strip(), flags=re.M).strip()
            ex2 = (json.loads(t2) or {}).get("exames") or []
            novos = sorted(set(_lidos1) | {str(e) for e in ex2})
            if novos != sorted(set(_lidos1)):
                a["exames_lidos_1a"] = sorted(set(_lidos1))   # rastro p/ auditoria
                a["releitura"] = True
            a["exames_lidos"] = novos
        except Exception:
            continue


_GTO_IMG_PROMPT = """Este anexo PODE ser uma GTO (Guia de Tratamento Odontológico do padrão
TISS) digitalizada ou fotografada. Você é um LEITOR/transcritor: NÃO decida nada,
apenas transcreva o que está escrito.

Responda APENAS JSON (sem markdown):
{"e_gto": true|false,
 "numero_guia": "<o número dos campos '2 - Nº Guia no Prestador' ou '7 - Nº da Guia
                 Atribuída pela Operadora'; use SÓ dígitos; "" se não achar>",
 "exames": ["<cada procedimento/exame listado, como está escrito>"],
 "campo_49": "<texto do campo '49 - Observação / Justificativa'; "" se vazio>",
 "profissional_solicitante": "<nome do campo '17 - Nome do Profissional Solicitante'; "" se não achar>",
 "conselho_numero": "<número do campo '19 - Número no Conselho' (o CRO do solicitante); só dígitos; "" se não achar>"}

Regras:
- "e_gto" só é true se o documento for mesmo a guia do convênio (tem numeração de
  campos tipo "2 -", "7 -", "49 -", tabela de procedimentos). Receituário, pedido
  de exame, RG, laudo ou nota fiscal => false.
- Transcreva LITERALMENTE. Não interprete, não complete, não deduza.
- Se não conseguir ler um trecho, omita em vez de adivinhar."""


# Download de anexo da guia pelo ACERVO DIGITAL do portal. Endpoint REAL,
# descoberto em 31/07 sniffando o popup da guia — os 4 palpites antigos davam
# 404 e o campo 49 da guia baixada do portal nunca chegava ao leitor (caso
# DAVI SANTANA, GTO 195456616: justificativa preenchida e ignorada):
#   GET /v1/gto/acervo-digital/imagem?numeroFicha=<gto>&sequencial=<n>&thumbnail=false
# O corpo vem em BASE64 (content-type text/plain) e ate PDF chega RENDERIZADO
# como imagem. `sequencial` e 1-based e segue a ORDEM de /v1/gto/imagens
# (validado ao vivo: seq 1..4 casou 1:1 com a lista da guia 195446697).

_MIME_POR_ASSINATURA = [
    (b"%PDF", "application/pdf"),
    (bytes([0x89]) + b"PNG", "image/png"),
    (bytes([0xFF, 0xD8, 0xFF]), "image/jpeg"),
]


def _mime_do_conteudo(b: bytes) -> str:
    for assin, mime in _MIME_POR_ASSINATURA:
        if b.startswith(assin):
            return mime
    return ""


def _baixar_anexo_portal(sess, gto, sequencial=None, _t=None):
    """(bytes, mime) de UM anexo da guia, pela POSICAO dele (sequencial 1-based)
    na lista que /v1/gto/imagens devolve.

    Aceita so o que TEM assinatura de PDF/PNG/JPEG depois de decodificar o
    base64: o portal responde 200 com HTML de login quando a sessao cai, e um
    HTML lido como imagem viraria leitura de lixo — o tipo de coisa que faz a
    IA "ver" o que nao existe."""
    if not gto or not sequencial:
        return None, ""
    try:
        import base64
        r = sess.get(f"{_ODO_API}/v1/gto/acervo-digital/imagem"
                     f"?numeroFicha={gto}&sequencial={int(sequencial)}"
                     f"&thumbnail=false", timeout=25)
        if r.status_code != 200:
            return None, ""
        bruto = (r.text or "").strip()
        if bruto.startswith("data:"):           # data-url: fica so o payload
            bruto = bruto.split(",", 1)[-1]
        # base64 pode vir quebrado em linhas; o padding tem que ser calculado
        # SEM o whitespace interno (achado do code review 31/07)
        bruto = re.sub(r"\s+", "", bruto)
        blob = base64.b64decode(bruto + "=" * (-len(bruto) % 4))
        mime = _mime_do_conteudo(blob)
        if not mime or len(blob) <= 512:
            return None, ""
        return blob, mime
    except Exception as e:
        if _t:
            _t(f"[API] download acervo-digital falhou (gto {gto} seq "
               f"{sequencial}): {str(e)[:60]}")
        return None, ""


def _anexos_via_api(token, gto):
    """(count, nomes, err) dos anexos pela API /v1/gto/imagens — a MESMA fonte
    AUTORITATIVA que a descoberta usa e confia (lista completa, com nomeArquivo e o
    flag imagemGTO). Serve de fallback quando o scrape do DOM (_anexos_count/
    _anexos_nomes) falha, tanto na trava da esteira quanto no ponto de escrita
    (upload_arquivos, via injecao). count = len da lista em HTTP 200; -1 em QUALQUER
    falha (sem token/gto, status != 200, resposta nao-lista, excecao). nomes = set de
    nomeArquivo (para a idempotencia por _chave_anexo). err = motivo. Retry 3x com
    backoff, espelhando o b588936. NUNCA conta por nomes do DOM (que sub-conta e
    faria duplicar num portal que nao remove anexo)."""
    if not token or not gto:
        return -1, set(), "sem token/gto"
    try:
        sess = requests.Session()
        _pxy = _odo_requests_proxies()
        if _pxy:
            sess.proxies.update(_pxy)
        sess.headers.update({"Authorization": token or "", "User-Agent": "Mozilla/5.0",
                             "Origin": "https://credenciado.odontoprev.com.br",
                             "Referer": "https://credenciado.odontoprev.com.br/"})
    except Exception as e:
        return -1, set(), f"setup: {type(e).__name__}: {str(e)[:80]}"
    _falha = None
    for _tent in range(3):
        try:
            r = sess.get(f"{_ODO_API}/v1/gto/imagens?numeroFicha={gto}", timeout=25)
            if r.status_code == 200:
                imgs = r.json()
                if isinstance(imgs, list):
                    nomes = {str(i.get("nomeArquivo", "")) for i in imgs
                             if isinstance(i, dict)}
                    return len(imgs), nomes, None
                _falha = "resposta 200 nao-lista"
            else:
                _falha = f"HTTP {r.status_code} {r.text[:80]!r}"
        except Exception as e:
            _falha = f"{type(e).__name__}: {str(e)[:100]}"
        if _tent < 2:
            time.sleep(1.5 * (_tent + 1))
    return -1, set(), _falha


def _anexos_count_api(token, gto, _t=None):
    """So a CONTAGEM (para a trava da esteira). Delega em _anexos_via_api. Retorna
    (n, err); loga o erro real quando falha, para o proximo run ter a pista."""
    n, _nomes, err = _anexos_via_api(token, gto)
    if n < 0 and _t:
        _t(f"[API] recontagem de anexos falhou (gto {gto}): {err}")
    return n, err


def _reconta_anexos(dom_n, api_fn):
    """Escolhe a contagem AUTORITATIVA de anexos, com o DOM como fonte PRIMARIA e a
    API (api_fn) SO como fallback quando o DOM falhou. api_fn e chamado apenas se
    dom_n < 0 — DOM valido (inclusive 0) nunca gasta a chamada. Retorna (n, fonte,
    err) com fonte em {"DOM","API","nenhuma"}.

    GUARDRAIL (a razao de a funcao existir): se as DUAS fontes falharem, devolve -1
    — que a trava de anexacao bloqueia. NUNCA devolve uma contagem positiva 'chutada'
    e NUNCA propaga excecao do fallback: anexar em duplicidade e irreversivel (o
    portal nao remove anexo)."""
    if isinstance(dom_n, int) and dom_n >= 0:
        return dom_n, "DOM", None
    try:
        api_n, api_err = api_fn()
    except Exception as e:
        return -1, "nenhuma", f"excecao no fallback: {type(e).__name__}: {str(e)[:80]}"
    if isinstance(api_n, int) and api_n >= 0:
        return api_n, "API", None
    return -1, "nenhuma", api_err


def _ha_leitura_no_nome(leituras, nome_gto):
    """True se ALGUM anexo lido (de QUALQUER tipo, inclusive 'documento'/RG, nao so
    'solicitacao') tem nome compativel com o da guia. Usado SO para a MENSAGEM:
    quando ha um RG no nome exato do paciente, a headline 'nenhum documento no nome
    deste paciente' e factualmente FALSA (caso CARINA, 28/07 — RG exato, solicitacao
    lida garbled). NAO decide anexacao: o casamento que libera upload continua so em
    _escolher_solicitacao (tipo 'solicitacao'), com as travas de sempre."""
    if not nome_gto:
        return False
    for a in (leituras or []):
        if isinstance(a, dict) and _nomes_compat(a.get("paciente_lido") or "", nome_gto):
            return True
    return False


def _ler_gto_por_imagem(gem, blob, mime, gto):
    """GTO ESCANEADA/FOTOGRAFADA: sem camada de texto, is_gto_pdf() não a reconhece
    e ela ainda virava CANDIDATA A SOLICITAÇÃO (podia ser anexada como se fosse o
    pedido do dentista). Aqui o Gemini apenas TRANSCREVE; quem valida é o código:
    confere o número da guia, canonicaliza os exames e aplica a MESMA regra do
    campo 49 (>=2 palavras alfabéticas não-boilerplate).

    Devolve {e_desta_guia, exames(set canônico), justificativa(bool)} ou None."""
    from google.genai import types
    try:
        r = gem.models.generate_content(
            model=_GEM_MODEL, config=_gem_cfg(),
            contents=[types.Part.from_bytes(data=blob, mime_type=mime), _GTO_IMG_PROMPT])
        _contar_tokens(r)
        txt = re.sub(r"^```json|^```|```$", "", (r.text or "").strip(), flags=re.M).strip()
        d = json.loads(txt) or {}
    except Exception:
        return None
    if not isinstance(d, dict) or not d.get("e_gto"):
        return None
    # O CÓDIGO confere a identidade da guia — sem isso, a GTO de outra visita
    # ditaria exames/justificativa desta.
    num = re.sub(r"\D", "", str(d.get("numero_guia") or ""))
    alvo = re.sub(r"\D", "", str(gto or ""))
    if not (num and alvo and num == alvo):
        return None
    exames = canon_exames(" ".join(str(e) for e in (d.get("exames") or [])))
    # mesma regra conservadora do campo 49 por texto (gto_utils._justif_por_texto)
    obs = str(d.get("campo_49") or "")
    palavras = [w for w in re.findall(r"[A-Za-zÀ-ú]{3,}", obs) if not _BOILER_49.search(w)]
    justif = len(palavras) >= 2 and not _BOILER_49.search(obs[:40])
    return {"e_desta_guia": True, "exames": exames, "justificativa": justif,
            "dentista": str(d.get("profissional_solicitante") or "").strip(),
            "cro": re.sub(r"\D", "", str(d.get("conselho_numero") or ""))}


# Formatos que o Gemini aceita direto. O resto era DESCARTADO EM SILÊNCIO — sem
# log, sem contagem — e a guia virava "nenhum documento com nome compatível", que é
# mentira: o documento existia e nunca foi olhado. Casos ALESSANDRA FERREIRA SENA e
# JANDIARA DA SILVA ALBINO (22/07): a solicitação estava em .tif, saída padrão de
# scanner. Agora converte em vez de jogar fora.
_MIME_DIRETO = {"pdf": "application/pdf", "png": "image/png",
                "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
_CONVERTER = {"tif", "tiff", "bmp", "gif"}


def preparar_anexo(filename, blob):
    """(mime, blob) pronto para o Gemini, ou (None, motivo) se não dá.
    Converte o que o Pillow lê e o Gemini não aceita."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext in _MIME_DIRETO:
        return _MIME_DIRETO[ext], blob
    if ext in _CONVERTER:
        try:
            img = Image.open(io.BytesIO(blob))
            # TIFF multipágina: só a 1ª. O pedido do dentista cabe numa folha.
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            return "image/jpeg", buf.getvalue()
        except Exception as e:
            return None, f"{ext} ilegível ({str(e)[:40]})"
    return None, f"formato .{ext or '?'} não suportado"


# Janela de busca do exame, em dias, em torno da data da guia. 0 desliga (volta ao
# comportamento antigo: só o dia exato). Regra do dono: "se a data for próxima,
# podemos seguir". Sete dias cobre remarcação e retorno sem alcançar o mês seguinte.
_JANELA_DIAS = int(os.environ.get("FATURAR_JANELA_DIAS", "7"))


def _offsets_janela(n):
    """Dias a tentar, do mais PRÓXIMO da guia para o mais distante: +1,-1,+2,-2..."""
    out = []
    for i in range(1, n + 1):
        out.append(i)
        out.append(-i)
    return out


def _data_mais(data, dias):
    """'DD/MM/AAAA' + N dias -> 'DD/MM/AAAA'. None se a data não for válida."""
    import datetime as _dt
    try:
        d = _dt.datetime.strptime(data, "%d/%m/%Y").date() + _dt.timedelta(days=dias)
        return d.strftime("%d/%m/%Y")
    except Exception:
        return None


def _exame_do_laudo(p):
    """Exame canônico embutido no nome do arquivo: LAUDO_<EXAME>_<acc>_TIPO.pdf"""
    m = re.match(r"LAUDO_(.+?)_\d+_", os.path.basename(p))
    return canon_exames(m.group(1)) if m else set()


def _acc_do_laudo(p):
    """Accession embutido no nome: LAUDO_<EXAME>_<acc>_TIPO.pdf -> '<acc>'.
    É a chave FORTE (identidade do exame no PRORADIS), ao contrário do nome do
    exame, que depende do _CANON reconhecer o termo."""
    m = re.match(r"LAUDO_(.+?)_(\d+)_", os.path.basename(p))
    return m.group(2) if m else None


def _laudos_sem_guia(excluidos, extras_acc):
    """Dos laudos EXCLUÍDOS de uma guia, os que são 'de fora' por PROCEDÊNCIA
    (accession em extras_acc = não veio do convênio → nenhuma guia o cobre): são
    laudos prontos SEM GUIA (exame particular ou guia esquecida). Distingue do
    excluído por TIPO de exame, que pode pertencer a OUTRA guia do mesmo paciente.
    Laudos excluídos são, por construção, PRONTOS (só entram na pasta se baixaram).
    Só LAUDO_* têm accession; ENTREGA_* e afins são ignorados.
    Retorna [{'accession','exame','arquivo'}]."""
    extras = {str(a) for a in (extras_acc or [])}
    out = []
    for lp in (excluidos or []):
        acc = _acc_do_laudo(lp)
        if acc and str(acc) in extras:
            exs = sorted(_exame_do_laudo(lp))
            out.append({"accession": str(acc),
                        "exame": ", ".join(exs) or "?",
                        "arquivo": os.path.basename(lp)})
    return out


def _filtrar_arquivos_da_gto(pasta, dec, extras_acc=None, convenio_acc=None):
    """Só sobem para a GTO os arquivos do CONVÊNIO. Exame PARTICULAR feito no mesmo
    dia não vai para a operadora — regra do dono.

    Duas fontes, nesta ordem:
      1) PROCEDÊNCIA (determinística): `extras_acc` são os accessions que NÃO vieram
         do relatório analítico — que já é consultado filtrado pelos convênios do
         plano. Laudo com esse accession é de fora, ponto. Sem adivinhação.
      2) Fallback (heurística antiga): exame do nome do arquivo fora dos exames da
         guia. Vale quando a procedência não chegou até aqui.

    Caso MISTO: as imagens ficam de fora, porque ENTREGA_*.jpg não diz a que exame
    pertence e atribuí-las seria chute. Mas a SOLICITAÇÃO vai junto — sem ela a
    guia era anexada só com o laudo, sem o documento que a própria decisão exigiu,
    e ainda assim registrada como faturada.
    Conservador: laudo não identificado é MANTIDO; GTO ilegível não filtra nada.
    Devolve (arquivos, excluidos, exames_fora)."""
    todos = sorted(os.listdir(pasta)) if pasta and os.path.isdir(pasta) else []
    cheio = [os.path.join(pasta, f) for f in todos]
    laudos = [p for p in cheio if os.path.basename(p).upper().startswith("LAUDO_")]
    fora = []

    # 1) procedência: accession que não veio do analítico do convênio
    if extras_acc:
        _ex = {str(a) for a in extras_acc}
        fora = [lp for lp in laudos if (_acc_do_laudo(lp) or "") in _ex]

    # 2) fallback pelo nome do exame — SÓ para laudo de procedência DESCONHECIDA.
    # O accession que veio do analítico já é, por construção, exame do convênio
    # (o relatório é consultado filtrado pelos convênios do plano). O rótulo do
    # exame no NOME DO ARQUIVO é chave fraca e às vezes diverge: LOARA (195215189,
    # 21/07) teve o laudo do accession 40335114 — interproximal no analítico e na
    # worklist — baixado como LAUDO_ATM_. O filtro leu "ATM", não achou na guia,
    # excluiu o laudo certo e a guia nem foi anexada. Prova forte vence rótulo.
    _conv = {str(a) for a in (convenio_acc or [])}
    if not fora:
        # exames DESTA guia; sem identificar a GTO, cai na união (comportamento antigo)
        alvo = (set((dec or {}).get("gto_exames_desta") or [])
                or set((dec or {}).get("gto_exames") or []))
        if not alvo:
            return cheio, [], []      # GTO ilegível -> não filtra (como antes)
        # Guia de DOCUMENTAÇÃO é cumprida pelos laudos dos componentes: sem isto,
        # LAUDO_PANORAMICA numa guia que diz 'documentacao' era descartado como
        # "exame particular" e a guia subia sem laudo.
        alvo = componentes_da_documentacao(alvo)
        for lp in laudos:
            if _conv and (_acc_do_laudo(lp) or "") in _conv:
                continue          # veio do analítico do convênio: nunca é "de fora"
            cex = _exame_do_laudo(lp)
            # exclui SÓ se o exame foi identificado E está fora da guia
            if cex and not (cex & alvo):
                fora.append(lp)

    if not fora:
        return cheio, [], []

    # MISTO: laudos do convênio + solicitação escolhida. Fora: laudos de outro
    # exame e as imagens (não atribuíveis).
    dentro = [lp for lp in laudos if lp not in fora]
    solic = [p for p in cheio
             if os.path.basename(p).upper().startswith("SOLICITACAO_")]
    exames_fora = sorted({e for lp in fora for e in _exame_do_laudo(lp)})
    excluidos = sorted(os.path.basename(x) for x in cheio
                       if x not in dentro and x not in solic)
    return dentro + solic, excluidos, exames_fora


def _build_by_norm(df):
    cod_col = "Cód. Pac" if "Cód. Pac" in df.columns else df.columns[1]
    ped_col = "Pedido" if "Pedido" in df.columns else df.columns[6]
    nome_col = "Paciente" if "Paciente" in df.columns else df.columns[2]
    by = {}
    for _, r in df.iterrows():
        nm = str(r[nome_col]).strip()
        lst = by.setdefault(normaliza_nome(nm), [])
        pac = next((p for p in lst if p["cod_pac"] == str(r[cod_col]).strip()), None)
        if not pac:
            pac = {"cod_pac": str(r[cod_col]).strip(), "nome": nm, "accessions": []}
            lst.append(pac)
        a = str(r[ped_col]).strip()
        if a and a not in pac["accessions"]:
            pac["accessions"].append(a)
        if len(nm) > len(pac["nome"]):
            pac["nome"] = nm
    return by


def _baixa_um(pg, ctx, by_norm, g, tmp, data):
    """ESTÁGIO 2 (download only): match + baixa laudo+imagens. Devolve item com
    _pac embutido (p/ o estágio de leitura). NÃO lê solicitação aqui."""
    t0 = time.monotonic()
    nn = g["nome_norm"]
    cands = by_norm.get(nn, [])
    if not cands:
        vistos, pref = set(), []
        for key, lst in by_norm.items():
            if _prefixo_casa(key, nn):
                for p in lst:
                    if p["cod_pac"] not in vistos:
                        vistos.add(p["cod_pac"]); pref.append(p)
        cands = pref
    if len(cands) > 1:
        return {"gto": g["gto"], "nome": g["nome"], "status": "AMBIGUO", "dt_dl": time.monotonic() - t0}
    if cands:
        pac = cands[0]
        wl = listar_worklist_por_pacientes(pg, data, [pac["nome"]])
    else:
        # FALLBACK: paciente fora do analítico. Aqui mora o maior risco do sistema —
        # a busca é por NOME e pode devolver gente diferente. Lógica portada do
        # fechar_dia.py (que já fazia certo): agrupa as linhas por paciente e só
        # segue se sobrar UM. Antes, bastava UMA linha casar (any) para o LOTE
        # INTEIRO de accessions ser aceito — inclusive de outros pacientes.
        wl = listar_worklist_por_pacientes(pg, data, [g["nome"]])

        def _casam_por_paciente(linhas, nn_alvo):
            """{nome_normalizado: [accessions]} apenas das linhas que casam com o
            alvo. Aceita nome IDÊNTICO (o _prefixo_casa sozinho rejeita igualdade,
            o que jogava nome exato no caminho da busca ampliada)."""
            out = {}
            for w in linhas:
                if not w.get("accession"):
                    continue
                wn = normaliza_nome(w.get("nome", ""))
                if wn == nn_alvo or _prefixo_casa(wn, nn_alvo) or _prefixo_casa(nn_alvo, wn):
                    out.setdefault(wn, []).append(w["accession"])
            return out

        casam = _casam_por_paciente(wl, nn)
        # Só encurta o nome se NADA casou. Cada tentativa é re-validada — o nome
        # encurtado alarga a busca e é justamente por onde entrava parente/homônimo.
        toks = g["nome"].split()
        while not casam and len(toks) > 2:
            toks = toks[:-1]
            wl = listar_worklist_por_pacientes(pg, data, [" ".join(toks)])
            casam = _casam_por_paciente(wl, nn)
        if len(casam) > 1:
            # dois pacientes distintos com nome compatível -> não dá pra saber qual
            return {"gto": g["gto"], "nome": g["nome"], "status": "AMBIGUO",
                    "dt_dl": time.monotonic() - t0}
        accs = sorted({a for v in casam.values() for a in v}) if casam else []
        # JANELA DE DATA — regra do dono (28/07): "se a data for próxima, podemos
        # seguir". O exame nem sempre é feito no dia da liberação da guia: paciente
        # remarca, volta depois, ou o exame é refeito. A busca olhava UM dia só
        # (00:00 a 23:59 da data da guia) e a guia morria em SEM_MATCH. Caso
        # DANIELLE GOMES DE JESUS PEREIRA — guia de 20/07, exames em 22/07.
        #
        # Guardas: o nome continua sendo validado do mesmo jeito (nada é afrouxado
        # aqui); e se aparecer exame em MAIS DE UM dia da janela, não dá pra saber
        # a qual guia cada um pertence — vira pendência, não chute.
        _dias_janela = []
        if not accs and _JANELA_DIAS:
            _achados = {}
            for _off in _offsets_janela(_JANELA_DIAS):
                _d = _data_mais(data, _off)
                if not _d:
                    continue
                try:
                    _wl2 = listar_worklist_por_pacientes(pg, _d, [g["nome"]])
                except Exception:
                    continue
                _c2 = _casam_por_paciente(_wl2, nn)
                if _c2:
                    if len(_c2) > 1:      # dois pacientes com nome compatível nesse dia
                        return {"gto": g["gto"], "nome": g["nome"], "status": "AMBIGUO",
                                "dt_dl": time.monotonic() - t0}
                    _achados[_d] = (_wl2, sorted({a for v in _c2.values() for a in v}))
            _dias_janela = sorted(_achados)
            if len(_achados) == 1:
                _d = _dias_janela[0]
                wl, accs = _achados[_d][0], _achados[_d][1]
                g["data_exame_real"] = _d
            elif len(_achados) > 1:
                return {"gto": g["gto"], "nome": g["nome"], "status": "AMBIGUO",
                        "dias_com_exame": _dias_janela,
                        "dt_dl": time.monotonic() - t0}
        if not accs:
            return {"gto": g["gto"], "nome": g["nome"], "status": "SEM_MATCH",
                    "janela": _JANELA_DIAS, "dt_dl": time.monotonic() - t0}
        pac = {"nome": g["nome"], "cod_pac": "WL" + accs[0], "accessions": accs}
    _data_exame = g.get("data_exame_real") or data
    res = _processar_paciente(pg, ctx, pac, wl, tmp, _data_exame)
    pasta = os.path.join(tmp, res["pasta"])
    nf = len(os.listdir(pasta)) if os.path.isdir(pasta) else 0
    status = "BAIXADO" if nf > 0 else "SEM_ARQUIVOS"
    return {"gto": g["gto"], "nome": pac["nome"], "status": status,
            "arquivos": nf, "imgs": res.get("imagens", {}).get("qtd", 0),
            "_pac": pac, "_pasta": pasta, "dt_dl": time.monotonic() - t0,
            # accessions que NÃO vieram do analítico (podem ser exame particular).
            # Vazio no fallback por nome (cod 'WL*'), onde não há analítico pra
            # comparar — aí o filtro cai na heurística antiga, como sempre fez.
            "extras_acc": res.get("accessions_extras") or [],
            "convenio_acc": res.get("accessions_convenio") or [],
            # exame achado em dia diferente do da guia: fica REGISTRADO, para a
            # operadora saber que a data não é a mesma
            "data_exame_real": g.get("data_exame_real"),
            # exames da guia lidos do PORTAL (fonte autoritativa) — usados quando o
            # PDF da GTO no prontuário vem sem a tabela de procedimentos
            "eventos_portal": g.get("eventos_portal") or [],
            # nº de anexos da guia que são CÓPIA ASSINADA da própria GTO
            # (imagemGTO=True na descoberta) — teto da trava do anexador
            "n_gto_copias": g.get("n_gto_copias"),
            # GTO baixada do PORTAL na descoberta (caso JOSETE): sem copiar estes
            # dois campos aqui, o blob morria neste return (o leitor lê
            # item.get("gto_portal_blob") e recebia sempre None — o 2º sinal
            # via guia do portal nunca rodava)
            "gto_portal_blob": g.get("gto_portal_blob"),
            "gto_portal_mime": g.get("gto_portal_mime")}


_DECISAO_PROMPT = """Acima estão VÁRIOS anexos do prontuário, indexados ([anexo 0], [anexo 1], ...).

Você é um LEITOR/transcritor. NÃO escolha qual anexo serve, NÃO decida nada, NÃO
compare com nenhuma GTO. Apenas LEIA CADA anexo e transcreva fielmente o que está
escrito. Quem decide é o sistema, não você.

Para CADA anexo, retorne um objeto com:
- "idx": o número do anexo ([anexo N] -> N)
- "tipo": um de "solicitacao" (pedido/requisição de exames feito por um dentista) |
  "laudo" (resultado/relatório de exame) | "documento" (RG/CNH/identidade) |
  "nota_fiscal" | "raio_x" (imagem de radiografia) | "outro"
- "legivel": true/false
- "paciente_lido": nome do paciente escrito no anexo (string; "" se não houver ou
  se estiver ilegível — NÃO invente, NÃO complete)
- "dentista_lido": nome do dentista que assina/carimba o pedido ("" se não houver)
- "cro_lido": número do CRO no carimbo, só dígitos ("" se não houver)
- "exames_lidos": os exames EFETIVAMENTE PEDIDOS neste anexo — NÃO o cardápio impresso.
  * FORMULÁRIO COM QUADRADINHOS/CAIXAS de opções: liste APENAS os exames cuja caixa
    está MARCADA (um X, tique, traço, círculo, rabisco ou preenchida). IGNORE as
    opções em branco — elas são só as opções impressas do papel, NÃO o pedido.
    Olhe COM ATENÇÃO qual linha tem a marca; NÃO liste as primeiras opções por
    padrão. Ex.: se só "Radiografia Periapical" está marcada, retorne ["periapical"]
    mesmo que "Panorâmica" apareça impressa acima em branco.
  * PEDIDO ESCRITO À MÃO / TEXTO LIVRE: liste os exames escritos.
  * VÁRIAS SEÇÕES: o pedido costuma ter MAIS DE UM BLOCO — uma lista numerada de
    exames radiográficos (ex.: "EXAMES RADIOGRÁFICOS: 1-panorâmica, 2-telerradiografia")
    E, separadamente, um bloco de "DOCUMENTAÇÃO", "FOTOS/FOTOGRAFIAS INTRA E EXTRA
    BUCAIS" ou "MODELOS". Transcreva TODAS as seções pedidas, uma por item — NÃO pare
    na primeira lista. Se houver fotografias ou modelos pedidos, inclua-os também.
  Regra de ouro: nunca inclua um exame só porque a palavra aparece IMPRESSA; vale o
  que está MARCADO ou ESCRITO como pedido.
  ex. de tokens: ["panoramica","periapical","interproximal","telerradiografia","documentacao","fotografias","modelos"]
- "data_solicitacao": data escrita no anexo, "DD/MM/AAAA" ou null
- "box_data": [ymin,xmin,ymax,xmax] (valores 0-1000) da data, ou null
- "box_assinatura": [ymin,xmin,ymax,xmax] (0-1000) da assinatura do dentista, ou null

Responda APENAS JSON (sem markdown):
{"anexos": [ {"idx":0, "tipo":"...", "legivel":true, "paciente_lido":"...",
"dentista_lido":"...", "cro_lido":"",
"exames_lidos":[...], "data_solicitacao":null, "box_data":null, "box_assinatura":null}, ... ]}
"""


def _box4(v):
    """Caixa [ymin,xmin,ymax,xmax] válida, ou None.

    O modelo às vezes devolve uma LISTA DE CAIXAS (ou mais de 4 números) e o
    desempacotamento `ymin, xmin, ymax, xmax = box` estourava com 'too many values
    to unpack'. A exceção era engolida como 'Erro ao editar imagem' e a guia virava
    pendência — com a documentação correta e conf=alta. Caso ALANNA VITORIA DOS
    PASSOS (195303194, 22/07). Aceita [a,b,c,d] ou [[a,b,c,d]]."""
    if isinstance(v, (list, tuple)) and len(v) == 1 and isinstance(v[0], (list, tuple)):
        v = v[0]
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = (float(x) for x in v)
    except Exception:
        return None
    # A anexacao e IRREVERSIVEL: uma caixa alucinada (invertida ou fora de
    # 0-1000) carimbaria a data em lugar errado do documento certo. Na duvida,
    # rejeita -> quem chama trata como "sem box" e a guia vai para revisao, em
    # vez de subir um pedido com a data desenhada por cima do texto. (Reforco do
    # code review 31/07; escala 0-1000 conforme o prompt.)
    if not (0 <= xmin < xmax <= 1000 and 0 <= ymin < ymax <= 1000):
        return None
    return [ymin, xmin, ymax, xmax]


def _parse_br_date(s):
    """'DD/MM/AAAA' (ou DD/MM/AA) -> date; None se não der."""
    try:
        import datetime as _dt
        p = re.findall(r"\d+", str(s))
        if len(p) < 3:
            return None
        d, m, y = int(p[0]), int(p[1]), int(p[2])
        if y < 100:
            y += 2000
        return _dt.date(y, m, d)
    except Exception:
        return None


def _date_from_name(s):
    """Extrai data de nome de arquivo: '...20260618_...' ou '2026-06-18'. None se não tiver."""
    try:
        import datetime as _dt
        m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", str(s))
        if not m:
            return None
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def _ler_anexos_um_a_um(gem, cands):
    """RESGATE do lote de leitura: cada anexo numa chamada propria, com o mesmo
    prompt, e o idx remapeado para a posicao real no lote.

    Caso SOPHIA (31/07): o lote fez o modelo degenerar (loop de 200k tokens,
    JSON truncado) e a guia morreu com erro tecnico. Lote de UM anexo nao
    degenera na pratica — e um anexo individualmente ilegivel so tira ELE da
    jogada, nao a guia inteira. Erro fatal (credito/chave) continua parando."""
    from google.genai import types
    out = []
    for i, (fn, mime, blob, saved) in enumerate(cands):
        try:
            r = gem.models.generate_content(
                model=_GEM_MODEL,
                contents=["[anexo 0]",
                          types.Part.from_bytes(data=blob, mime_type=mime),
                          _DECISAO_PROMPT],
                config=_gem_cfg())
            _contar_tokens(r)
            txt = re.sub(r"^```json|^```|```$", "", (r.text or "").strip(),
                         flags=re.M).strip()
            data = json.loads(txt)
            ls = (data.get("anexos") if isinstance(data, dict) else data) or []
            for a in ls:
                if isinstance(a, dict):
                    a["idx"] = i
                    out.append(a)
                    break               # 1 anexo enviado -> 1 leitura
        except Exception as e:
            if _gem_fatal(e):
                raise
            # anexo individual que nao leu: segue sem ele
    return out


def _motivo_nao_cobre(pede, falta, cn):
    """Motivo da guia que não faturou por cobertura. Separa dois casos que ANTES
    saíam com a MESMA mensagem (culpando a clínica):
    - PEDIDO ILEGÍVEL (cn vazio = o Gemini não decifrou os exames escritos; caso
      MARIA CLARA, 'periapical' -> 'perigeed'): é limitação de leitura NOSSA, não
      falta de pedido -> conferir e anexar à mão. Preferência do dono (02/08).
    - PEDIDO LEGÍVEL que não cobre: falta X -> pedir à clínica (como antes)."""
    if not cn:
        return (f"NÃO FATUROU porque a caligrafia do pedido do dentista está ilegível "
                f"— o robô não conseguiu ler os exames escritos no pedido mais recente. "
                f"A guia autoriza {lista_amigavel(pede)} e o pedido pode até incluir isso, "
                f"mas a letra não foi decifrada. O QUE FAZER: conferir o pedido no "
                f"prontuário e anexar à mão (é limitação de leitura, não falta de pedido).")
    return (f"NÃO FATUROU porque o pedido do dentista não cobre tudo que a guia "
            f"autoriza. FALTA no pedido: {lista_amigavel(falta)}. A guia autoriza "
            f"{lista_amigavel(pede)}; o pedido encontrado no prontuário pede "
            f"{lista_amigavel(cn)}. O QUE FAZER: pedir à clínica um pedido que inclua "
            f"{lista_amigavel(falta)}.")


def _decidir(gem, pg, ctx, pac, pasta_dl, review_dir=None, gto=None,
             eventos_portal=None, gto_blob=None, gto_mime=""):
    """ESTÁGIO 3 (decisão): baixa anexos do prontuário, extrai os exames da GTO e
    manda TUDO pro Gemini escolher a solicitação certa + decidir. NÃO anexa.
    Devolve plano (laudo+imgs sempre; solicitação se a IA confiar) + a decisão.
    Se review_dir/gto, salva os candidatos p/ a página de revisão."""
    from google.genai import types
    out = {"anexos": 0, "gto_exames": [], "decisao": None, "erro": None,
           "plano_laudo_imgs": [], "plano_solicitacao": None,
           "candidatos": [], "solic_idx": None, "justificativa": None}
    if pasta_dl and os.path.isdir(pasta_dl):
        out["plano_laudo_imgs"] = sorted(os.listdir(pasta_dl))
    try:
        lista = anexos_do_paciente(pg, pac["nome"], pac["cod_pac"])
    except Exception as e:
        out["erro"] = f"anexos: {str(e)[:80]}"; return out
    out["anexos"] = len(lista)
    cj = {ck["name"]: ck["value"] for ck in ctx.cookies()}
    sess = requests.Session(); sess.cookies.update(cj)
    sess.headers.update({"User-Agent": "Mozilla/5.0", "Referer": f"{BASE}/patients"})
    att_dir = tempfile.mkdtemp(prefix="_att_")
    # Documento de paciente em disco: quem chama APAGA assim que a decisão sai
    # (ver leitor()). Sem isso, até 30 anexos de prontuário POR GTO ficavam
    # retidos indefinidamente — inclusive nas rodadas automáticas.
    out["_att_dir"] = att_dir
    # ordena do MAIS NOVO pro mais antigo (id desc): garante que a solicitação
    # recente entre mesmo em prontuário grande/com histórico de anos.
    def _id_key(it):
        try:
            return int(re.sub(r"\D", "", str(it.get("id", ""))) or 0)
        except Exception:
            return 0
    lista = sorted(lista, key=_id_key, reverse=True)
    cands_raw, gto_ex, justif_ok = [], set(), False
    gto_ex_desta = set()   # exames SÓ da GTO desta guia (a união serve p/ cobertura,
                           # mas não pode ir pra mensagem: acusaria a clínica de não
                           # ter pedido exame que esta guia nunca pediu)
    _disp_laudo = None   # None=nenhuma GTO lida ainda; vira False se qualquer GTO exigir laudo
    _gtos_desta = 0      # quantas GTOs do prontuário são desta guia (nº confere)
    for it in lista[:30]:
        ext = it["filename"].lower().rsplit(".", 1)[-1] if "." in it["filename"] else ""
        try:
            blob = sess.get(it["url"], timeout=60).content
        except Exception:
            continue
        path = os.path.join(att_dir, re.sub(r"[^A-Za-z0-9._-]+", "_", it["filename"]) or it["id"])
        with open(path, "wb") as f:
            f.write(blob)
        if ext == "pdf" and is_gto_pdf(path):     # pdf da GTO -> exames + justificativa
            # É a GTO DESTA guia ou de outra visita do mesmo paciente? Justificativa
            # e dispensa-de-laudo só podem vir da GTO que está sendo faturada — senão
            # uma GTO antiga (outra guia, campo 49 preenchido) dispensaria a
            # solicitação da atual.
            _desta = gto_e_desta_guia(path, gto) if gto is not None else False
            try:
                _ex_pdf = gto_exames(path)
                # SÓ a GTO DESTA guia (número confere) alimenta a referência de
                # cobertura. Caso MARIA CLARA (GTO 195436162, 27/07): o prontuário
                # acumula GTOs de ANOS e episódios diferentes — uma guia doc-orto
                # de 2025 no meio dos anexos. Antes, os exames de TODAS as GTOs
                # entravam na união (gto_ex), e a guia atual (que pede só
                # periapical) passava a "exigir" documentação/ATM que ela nunca
                # pediu — cobertura falsa-negativa. A referência correta é a GTO
                # DESTA guia; quando ela não está legível no prontuário, os
                # EVENTOS DO PORTAL (fonte autoritativa por ficha) preenchem
                # gto_ex logo abaixo. GTO de outra guia NÃO entra.
                if _desta:
                    gto_ex |= _ex_pdf
                    gto_ex_desta |= _ex_pdf
            except Exception:
                pass
            if _desta:
                _gtos_desta += 1
                try:
                    # dispensa laudo SÓ se a GTO é exclusivamente modelo/fotografia.
                    # Conservador: se qualquer GTO desta guia exigir laudo, exige.
                    _d = gto_dispensa_laudo(path)
                    _disp_laudo = _d if _disp_laudo is None else (_disp_laudo and _d)
                except Exception:
                    _disp_laudo = False
                try:
                    if extrair_observacao(path).get("status") == "PREENCHIDO":
                        justif_ok = True
                except Exception:
                    pass
                try:
                    # nome e TEXTO da guia: usados como 2o sinal quando a leitura do
                    # nome do paciente falha (carimbo do dentista / CRO)
                    out["dentista_gto"] = gto_solicitante(path) or out.get("dentista_gto")
                    out["gto_texto"] = gto_texto(path) or out.get("gto_texto")
                except Exception:
                    pass
            continue
        mime, blob2 = preparar_anexo(it["filename"], blob)
        if mime:
            if blob2 is not blob:
                out.setdefault("convertidos", []).append(it["filename"])
            cands_raw.append((it["filename"], mime, blob2))
        else:
            # blob2 traz o motivo. NÃO some mais em silêncio.
            out.setdefault("descartados", []).append(f"{it['filename']}: {blob2}")
    # GTO ESCANEADA/FOTOGRAFADA: sem camada de texto, is_gto_pdf() não a reconhece,
    # a guia fica "ilegível" (vira pendência) e — pior — ela mesma entra como
    # candidata a SOLICITAÇÃO. Aqui o Gemini apenas TRANSCREVE a guia e o código
    # valida (nº da guia, exames canônicos, regra do campo 49). Só roda quando a
    # GTO desta guia NÃO foi encontrada pelo caminho normal, então não custa nada
    # no fluxo comum.
    if gem is not None and gto is not None and _gtos_desta == 0 and cands_raw:
        _restantes = []
        for fn, mime, blob in cands_raw:
            if _gtos_desta:                      # já achei a GTO desta guia
                _restantes.append((fn, mime, blob)); continue
            lida = _ler_gto_por_imagem(gem, blob, mime, gto)
            if not lida:
                _restantes.append((fn, mime, blob)); continue
            _gtos_desta += 1
            gto_ex |= lida["exames"]
            gto_ex_desta |= lida["exames"]
            if lida["justificativa"]:
                justif_ok = True
            # NÃO volta para cands_raw: é a GTO, não a solicitação.
            out["gto_lida_por_imagem"] = fn
        cands_raw = _restantes

    # FONTE AUTORITATIVA: os eventos da ficha no PORTAL. O PDF da GTO no prontuário
    # às vezes traz só os RÓTULOS dos campos, sem a tabela de procedimentos — aí
    # gto_exames() volta vazio e a guia caía em "GTO ilegível (sem exames de
    # referência)" mesmo com tudo certo (casos ALEXSANDRO/EDIMAR/JORGE, 25-26/07).
    # O portal é a própria operadora dizendo o que a guia autoriza.
    if eventos_portal:
        _ex_portal = canon_exames(" ".join(str(e) for e in eventos_portal))
        if _ex_portal:
            out["exames_portal"] = sorted(_ex_portal)
            if not gto_ex_desta:
                gto_ex_desta |= _ex_portal
            if not gto_ex:
                gto_ex |= _ex_portal

    # A GUIA BAIXADA DO PORTAL. So entra quando o prontuario NAO trouxe a guia desta
    # visita (_gtos_desta == 0) — e o caso em que o sistema ficava sem dentista, sem
    # CRO e sem campo 49, e portanto sem o segundo sinal. Caso JOSETE DIAS DE
    # SANTANA: o carimbo estava legivel, o CRO foi lido certo, e nao havia contra o
    # que comparar porque a guia so existia no RedeUna.
    #
    # Vale mais que a do prontuario em um ponto: foi pedida por numeroFicha, entao E
    # desta guia por construcao. Ainda assim _ler_gto_por_imagem confere o numero —
    # cinto e suspensorio custam nada aqui.
    if gem is not None and gto is not None and gto_blob and _gtos_desta == 0:
        _lp = _ler_gto_por_imagem(gem, gto_blob, gto_mime or "image/png", gto)
        if _lp:
            out["gto_do_portal"] = True
            if _lp.get("dentista") and not out.get("dentista_gto"):
                out["dentista_gto"] = _lp["dentista"]
            if _lp.get("cro"):
                # _dentista_confere procura o CRO dentro do TEXTO da guia; aqui o
                # texto e sintetico, so para carregar o numero ate la.
                out["gto_texto"] = ((out.get("gto_texto") or "")
                                    + " CRO " + _lp["cro"] + " ")
            if _lp.get("justificativa"):
                justif_ok = True
            if _lp.get("exames"):
                gto_ex |= _lp["exames"]
                gto_ex_desta |= _lp["exames"]
            _dbg = (f"dentista={_lp.get('dentista') or '-'} CRO={_lp.get('cro') or '-'} "
                    f"exames={sorted(_lp.get('exames') or [])} "
                    f"campo49={'sim' if _lp.get('justificativa') else 'nao'}")
            out["gto_portal_lida"] = _dbg

    out["gto_exames"] = sorted(gto_ex)
    out["gto_exames_desta"] = sorted(gto_ex_desta)
    # Só dispensa laudo se a dispensa veio da GTO DESTA guia. Sem a GTO desta guia
    # no prontuário, NUNCA dispensa (regra do dono: nada dispensa laudo além de
    # modelo/fotografia — e só dá pra saber isso lendo a GTO certa).
    out["dispensa_laudo"] = bool(_disp_laudo) and _gtos_desta > 0
    out["gto_desta_guia"] = _gtos_desta

    # REGRA: GTO com justificativa (campo 49) -> solicitação DISPENSADA. Nem toca
    # nos anexos do prontuário (não salva, não manda pro Gemini). Só laudo+imgs.
    if justif_ok:
        out["justificativa"] = "PREENCHIDA"
        out["decisao"] = {"anexar": False, "justificativa": True,
                          "motivo": "GTO tem justificativa (campo 49) — solicitação dispensada"}
        return out

    # sem justificativa -> precisa da solicitação: agora sim salva candidatos + Gemini
    # (os 15 mais novos — já ordenados do mais recente pro mais antigo)
    cands = []
    for fn, mime, blob in cands_raw[:15]:
        saved = None
        if review_dir and gto is not None:
            gdir = os.path.join(review_dir, str(gto))
            os.makedirs(gdir, exist_ok=True)
            saved = f"{len(cands)}__{re.sub(r'[^A-Za-z0-9._-]+', '_', fn) or 'anexo'}"
            try:
                with open(os.path.join(gdir, saved), "wb") as f:
                    f.write(blob)
            except Exception:
                saved = None
        cands.append((fn, mime, blob, saved))
    out["candidatos"] = [{"idx": i, "nome": c[0], "arquivo": c[3]} for i, c in enumerate(cands)]
    # FUNIL: quantos anexos o prontuário tinha, quantos viraram candidatos, quantos
    # foram descartados e por quê. Sem isto, "sem anexo candidato" e "nenhum nome
    # compatível" eram indistinguíveis de "o arquivo foi jogado fora sem ser lido".
    out["funil"] = {"prontuario": len(lista), "baixados": min(len(lista), 30),
                    "candidatos": len(cands), "descartados": len(out.get("descartados") or []),
                    "convertidos": len(out.get("convertidos") or [])}
    if not cands:
        out["decisao"] = {"anexar": False, "motivo": (
            "NÃO FATUROU porque não há nenhum pedido do dentista anexado ao "
            "prontuário deste paciente. O sistema abriu o prontuário e não "
            f"encontrou nenhum documento que sirva como pedido"
            + (f" ({len(out.get('descartados') or [])} anexo(s) não puderam ser "
               f"lidos: {'; '.join(out.get('descartados') or [])[:160]})"
               if out.get("descartados") else "")
            + ". O QUE FAZER: pedir à clínica que anexe o pedido no prontuário.")}
        return out
    if _gem_estado["fatal"]:
        # já sabemos que a leitura está fora do ar nesta execução: falha na hora
        out["erro"] = ("leitura indisponível nesta execução: "
                       + str(_gem_estado["fatal"])[:140])
        return out
    contents = []
    for i, (fn, mime, blob, saved) in enumerate(cands):
        contents.append(f"[anexo {i}]")
        contents.append(types.Part.from_bytes(data=blob, mime_type=mime))
    contents.append(_DECISAO_PROMPT)
    for tent in range(3):
        try:
            r = gem.models.generate_content(model=_GEM_MODEL, contents=contents,
                                            config=_gem_cfg())
            _contar_tokens(r)
            txt = re.sub(r"^```json|^```|```$", "", (r.text or "").strip(), flags=re.M).strip()
            try:
                data = json.loads(txt)
            except Exception:
                # Lote degenerou (caso SOPHIA 31/07: loop de geracao, JSON
                # truncado). Com o teto de saida a falha e rapida; o resgate e
                # ler os anexos UM A UM em vez de re-tentar o mesmo lote.
                data = _ler_anexos_um_a_um(gem, cands)
                if not data:
                    raise
                out["leitura_um_a_um"] = True
            leituras = (data.get("anexos") if isinstance(data, dict) else data) or []

            # ── O CÓDIGO ESCOLHE a solicitação (o Gemini só LEU/transcreveu) ──────
            # ALVO DA COBERTURA — precedência explícita:
            #   1) a GTO DESTA guia (nº conferido no PDF, ou lida por imagem);
            #   2) os eventos DESTA ficha no portal (a operadora dizendo o que autorizou);
            #   3) a união das GTOs do prontuário — ÚLTIMO recurso.
            # Usar (3) como alvo é o bug que reprovava guia correta: com duas guias no
            # prontuário (uma de panorâmica, outra de interproximal), a solicitação da
            # panorâmica era cobrada de cobrir interproximal e virava pendência — e a
            # mensagem, que já usava os exames DESTA guia, saía com as duas listas
            # idênticas ("pede [panoramica] mas a GTO pede [panoramica]").
            _alvo_ex = alvo_cobertura(gto_ex_desta, out.get("exames_portal"), gto_ex)
            # PRONTUÁRIO CONFIRMADO como sendo do paciente: a GTO da PRÓPRIA guia
            # (número confere) está arquivada nele — prova forte de que a pasta é
            # deste paciente. Habilita a corroboração do fallback de nome ilegível
            # (caso MAYSA). O ramo fraco "algum anexo lê nome compatível" foi
            # removido no code review: confirmava a pasta de forma desacoplada do
            # documento aceito, e para uma ação IRREVERSÍVEL preferimos a prova
            # forte (a GTO da guia presente).
            _pront_ok = (_gtos_desta > 0)
            _det = {}
            idx, a, _motivo = _escolher_solicitacao(leituras, pac["nome"], _alvo_ex,
                                                    len(cands), out.get("dentista_gto") or "",
                                                    _det, out.get("gto_texto") or "",
                                                    prontuario_confirmado=_pront_ok)
            # Falhou na cobertura OU na identidade? Releitura dirigida e nova
            # decisão. NAO_COBRE: manuscrito sub-lido (leu "periapical", perdeu
            # "panorâmica"). PACIENTE_INCOMPATIVEL: um pedido IMPRESSO que lê o
            # nome certo pode ter sido mal-classificado como "documento" — reler
            # o tipo pode salvá-lo sem depender da letra (parte 3, caso MAYSA).
            if idx is None and _motivo in ("NAO_COBRE", "PACIENTE_INCOMPATIVEL"):
                # relê exames dos que já eram solicitação SÓ quando o problema é
                # cobertura (no "nome não bate" não há candidato de nome compatível
                # a reler); nos dois casos, relê o TIPO dos não-classificados.
                if _motivo == "NAO_COBRE":
                    _reler_exames_focado(gem, cands, leituras, pac["nome"])
                _n2 = _reler_nao_classificados(gem, cands, leituras,
                                               nome_gto=pac["nome"])
                if _n2:
                    out["releitura_achou"] = _n2
                _det = {}
            idx, a, _motivo = _escolher_solicitacao(leituras, pac["nome"], _alvo_ex,
                                                    len(cands), out.get("dentista_gto") or "",
                                                    _det, out.get("gto_texto") or "",
                                                    prontuario_confirmado=_pront_ok)
            candidato_valido = idx is not None
            if candidato_valido:
                dec = {"indice_solicitacao": idx, "paciente_lido": a.get("paciente_lido"),
                       "exames_lidos": a.get("exames_lidos"),
                       "data_solicitacao": a.get("data_solicitacao"),
                       "outras_solicitacoes": (_det or {}).get("outras", 0),
                       "box_data": a.get("box_data"), "box_assinatura": a.get("box_assinatura"),
                       "legivel": True, "tipo": a.get("tipo"), "exames_batem": True,
                       "paciente_bate": True, "confianca": "alta", "anexar": True,
                       "leituras": leituras}
            else:
                # transparência p/ a pendência: o que foi LIDO do candidato que a
                # decisão AVALIOU (não outro qualquer). Só usado como último recurso
                # quando _det não trouxe os exames — nunca para "corrigir" a lista.
                _lidos = []
                for _a2 in leituras:
                    if (isinstance(_a2, dict) and _a2.get("tipo") == "solicitacao"
                            and _nomes_compat(_a2.get("paciente_lido") or "", pac["nome"])):
                        _lidos = _a2.get("exames_lidos") or []
                        break
                if _motivo == "NAO_COBRE":
                    # _cn e _falta TÊM de descrever o MESMO candidato — o que a
                    # decisão avaliou (_det). Caso MARIA CLARA (GTO 195436162,
                    # 27/07): quando o candidato avaliado tinha exames vazios, o
                    # `or` caía no fallback e mostrava os exames de OUTRO anexo —
                    # saía "FALTA periapical, mas o pedido pede [...periapical...]",
                    # contradição pura. `is None` preserva a lista vazia do
                    # candidato certo em vez de trocar de candidato.
                    _cn = _det.get("lidos")
                    if _cn is None:
                        _cn = sorted(expande_documentacao(
                            canon_exames(" ".join(str(e) for e in _lidos),
                                         recuperar=True)))   # PEDIDO: mesma recuperação da decisão
                    # MESMO conjunto usado no critério (_alvo_ex). Mensagem e regra
                    # têm de ser a mesma coisa: quando divergiam, a pendência saía
                    # com as duas listas idênticas e parecia um absurdo lógico.
                    # 'documentacao_completa' é token INTERNO (distingue a completa
                    # do controle). Vazava para a mensagem da operadora, que via
                    # "GTO pede ['documentacao', 'documentacao_completa']" — ruído.
                    _pede = sorted(x for x in _alvo_ex if not str(x).startswith("documentacao_"))
                    _falta_bruto = (_det.get("falta") if _det.get("falta") is not None
                                    else (set(_pede) - set(_cn)))
                    _falta = sorted(x for x in _falta_bruto
                                    if not str(x).startswith("documentacao_"))
                    # O que falta pode ser SO um token interno (documentacao_completa).
                    # Escondia-lo do texto deixava a mensagem dizendo "FALTA: nenhum"
                    # numa guia reprovada — e "peca a clinica um pedido que inclua
                    # nenhum". Caso LAIS ZAA GUIA SANTOS (24/07 Centro). Quando for
                    # esse o caso, explica o que realmente falta em portugues.
                    if not _falta and _falta_bruto:
                        _falta = ["a especificacao de que a documentacao e COMPLETA "
                                  "(telerradiografia, fotografias e modelos)"]
                    # PEDIDO ILEGÍVEL (o Gemini não leu os exames, _cn vazio) recebe
                    # mensagem diferente — grafia ilegível, anexar à mão — em vez de
                    # culpar a clínica por um pedido que EXISTE (caso MARIA CLARA). O
                    # pedido legível que não cobre segue como antes (pedir à clínica).
                    _motivo = _motivo_nao_cobre(_pede, _falta, _cn)
                elif _motivo == "PACIENTE_INCOMPATIVEL":
                    if _ha_leitura_no_nome(leituras, pac["nome"]):
                        # CARINA (28/07): HA um RG no nome EXATO do paciente, mas a
                        # solicitacao veio mal-lida/ilegivel ou pede exame diferente do
                        # que a guia autoriza — dizer "nenhum documento no nome" era
                        # mentira. So o TEXTO muda; a guia segue pendencia (a decisao
                        # e a anexacao nao mudam, dec ja sai com anexar=False).
                        _motivo = (
                            "NÃO FATUROU porque a solicitação do dentista não pôde ser "
                            "confirmada para esta guia — há documento no nome do paciente "
                            "no prontuário, mas a solicitação encontrada está mal-lida/"
                            "ilegível ou pede exame diferente do que a guia autoriza. "
                            "O QUE FAZER: abrir o prontuário, conferir a solicitação e, "
                            "se ela cobrir o exame da guia, anexar à mão.")
                    else:
                        _motivo = (
                            "NÃO FATUROU porque nenhum documento do prontuário está no nome "
                            "deste paciente. O prontuário tem anexos, mas o nome lido em cada "
                            "um não confere com o nome da guia — pode ser pedido em nome de "
                            "outra pessoa (mãe, responsável) ou cadastro divergente. "
                            "O QUE FAZER: conferir no prontuário se o pedido é mesmo deste "
                            "paciente e, se for, corrigir o nome no cadastro.")
                elif _motivo == "GTO_ILEGIVEL":
                    _motivo = (
                        "NÃO FATUROU porque o sistema não conseguiu ler quais exames a "
                        "guia autoriza — nem no PDF da guia, nem na ficha do portal. "
                        "Sem saber o que a guia pede, não dá para conferir o pedido. "
                        "O QUE FAZER: abrir a guia no portal e conferir manualmente. "
                        "(Falha nossa, não da clínica.)")
                elif _motivo == "LEITURA_VAZIA":
                    _motivo = (
                        "NÃO FATUROU porque a leitura dos anexos do prontuário não "
                        "retornou nada. Normalmente é falha temporária da leitura. "
                        "O QUE FAZER: reprocessar o dia. (Falha nossa, não da clínica.)")
                # NOMES LIDOS em cada anexo — sem isto nao da para saber se a
                # guia travou por cadastro divergente de verdade ou por rigor da
                # comparacao. Eram 18 guias em 21-24/07 e a unica evidencia
                # gravada era "nenhum documento com nome compativel", que nao
                # ajuda ninguem a agir.
                _nl = []
                for _a3 in (leituras or []):
                    if not isinstance(_a3, dict):
                        continue
                    _pl = (_a3.get("paciente_lido") or "").strip()
                    _dl = (_a3.get("dentista_lido") or "").strip()
                    _cr = (_a3.get("cro_lido") or "").strip()
                    _ex3 = ", ".join(str(e) for e in (_a3.get("exames_lidos") or []))[:70]
                    _nl.append(f"[{_a3.get('idx')}/{_a3.get('tipo') or '?'}"
                               + ("/2a" if _a3.get("_releitura") else "") + "]"
                               + (f" paciente={_pl!r}" if _pl else " paciente=(ilegivel)")
                               + (f" dentista={_dl!r}" if _dl else "")
                               + (f" CRO={_cr}" if _cr else "")
                               + (f" data={_a3.get('data_solicitacao')}"
                                  if _a3.get("data_solicitacao") else "")
                               + (f" exames=[{_ex3}]" if _ex3 else ""))
                dec = {"indice_solicitacao": None, "exames_batem": False,
                       "exames_lidos": _lidos, "paciente_bate": False, "anexar": False,
                       "motivo": _motivo, "leituras": leituras,
                       "nomes_lidos": " | ".join(_nl)[:3000]}

            # Se o candidato foi VALIDADO pelo codigo, avalia manipulação de data
            if candidato_valido:
                fn_candidato, mime, blob, saved = cands[idx]
                data_lida_str = dec.get("data_solicitacao")
                data_lida = _parse_br_date(data_lida_str) if data_lida_str else None
                hoje = datetime.now().date()
                
                precisa_manipular = False
                tipo = None
                
                if not data_lida:
                    precisa_manipular = True; tipo = 'inserir'
                elif (hoje - data_lida).days > 60:
                    precisa_manipular = True; tipo = 'atualizar'
            
                # Solicitação em PDF: o ajuste de data edita IMAGEM (PIL). Antes um PDF
                # com data VENCIDA ia direto pra revisão. Agora RENDERIZA a página do
                # PDF para imagem (PyMuPDF) e segue o MESMO ajuste — box_data é 0-1000,
                # mapeia igual. Só cai em revisão se a renderização falhar E a data
                # estiver vencida. 'inserir' + render falhou = fluxo antigo (sobe o PDF
                # como está). Caso SIDNEY (27/07): PDF com data lida como vencida.
                if precisa_manipular and "image" not in mime.lower():
                    _rend = _pdf_para_imagem(blob)
                    if _rend:
                        blob, mime = _rend
                        # o conteúdo virou PNG -> troca a extensão do NOME, senão o
                        # arquivo salvo/enviado teria bytes PNG com nome .pdf e o
                        # convênio rejeitaria. O nome saneado é usado no save abaixo.
                        fn_candidato = re.sub(r"\.[A-Za-z0-9]+$", "", fn_candidato) + ".png"
                    elif tipo == 'atualizar':
                        dec["anexar"] = False
                        dec["motivo"] = ("Solicitação em PDF com data vencida e a página "
                                         "não pôde ser renderizada para ajustar — revisar")
                        candidato_valido = False
                if candidato_valido and precisa_manipular and "image" in mime.lower():
                    try:
                        img = Image.open(io.BytesIO(blob))
                        draw = ImageDraw.Draw(img)
                        largura, altura = img.size
                        nova_data = hoje.strftime("%d/%m/%Y")
                        
                        tamanho_fonte = max(24, int(altura * 0.025)) # Aprox 2.5% da altura da imagem
                        try:
                            font = ImageFont.truetype("arial.ttf", tamanho_fonte)
                        except Exception:
                            try:
                                font = ImageFont.truetype("LiberationSans-Regular.ttf", tamanho_fonte)
                            except Exception:
                                font = ImageFont.load_default()
            
                        _editou = False   # a edição REALMENTE aconteceu?
                        _bd = _box4(dec.get("box_data"))
                        # Data vencida e a IA não devolveu ONDE a data está: em vez
                        # de mandar direto para revisão manual (caso ESTER, 27/07),
                        # pergunta a localização num anexo só — acerta muito mais.
                        if tipo == 'atualizar' and not _bd:
                            _bd2, _ = _reler_box_data(gem, cands, idx)
                            if _bd2:
                                _bd = _bd2
                        if tipo == 'atualizar' and _bd:
                            ymin, xmin, ymax, xmax = _bd
                            # Apaga data antiga com retângulo branco
                            draw.rectangle([int((xmin/1000)*largura), int((ymin/1000)*altura),
                                            int((xmax/1000)*largura), int((ymax/1000)*altura)], fill="white")
                            # Reescreve a nova data no mesmo lugar da antiga
                            draw.text((int((xmin/1000)*largura), int((ymin/1000)*altura)), nova_data, fill="black", font=font)
                            _editou = True
                        elif tipo == 'inserir':
                            # Prefere a área de assinatura informada pela IA; fallback: centro-inferior
                            box_ass = _box4(dec.get("box_assinatura"))
                            if box_ass:
                                ymin_a, xmin_a, ymax_a, xmax_a = box_ass
                                # Insere logo abaixo da área de assinatura, centralizado horizontalmente
                                pos_x = int(((xmin_a + xmax_a) / 2 / 1000) * largura)
                                pos_y = int((ymax_a / 1000) * altura) + 4
                            else:
                                # Fallback: 50% da largura, 85% da altura
                                pos_x = int(largura * 0.50)
                                pos_y = int(altura * 0.85)
                            draw.text((pos_x, pos_y), nova_data, fill="black", font=font)
                            _editou = True

                        # 'atualizar' SEM box_data não caía em nenhum ramo: nada era
                        # desenhado, mas o registro dizia "Data ajustada" e o documento
                        # seguia pra anexação com a data VELHA. Agora vira pendência.
                        if _editou:
                            img_byte_arr = io.BytesIO()
                            if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                            img.save(img_byte_arr, format=img.format if img.format else "JPEG")
                            blob = img_byte_arr.getvalue() # Atualiza o arquivo em memória
                            dec["data_solicitacao"] = nova_data; dec["anexar"] = True
                            dec["motivo"] = "Data ajustada automaticamente."
                        else:
                            dec["anexar"] = False
                            dec["motivo"] = ("Solicitação com data vencida e o sistema não "
                                             "localizou onde ajustar (sem box da data) — revisar")
                            candidato_valido = False
                    except Exception as e:
                        # Manipulação de data FALHOU -> NÃO anexa (nao pode faturar com
                        # a data nao-ajustada). Invalida o candidato tambem.
                        dec["anexar"] = False; dec["motivo"] = f"Erro ao editar imagem: {str(e)}"
                        candidato_valido = False

            out["decisao"] = dec
            # Salva o arquivo (original ou modificado) — SÓ se o CÓDIGO validou
            if candidato_valido:
                out["plano_solicitacao"] = fn_candidato
                out["solic_idx"] = idx
                if pasta_dl and os.path.isdir(pasta_dl):
                    sname = "SOLICITACAO_" + (re.sub(r"[^A-Za-z0-9._-]+", "_", fn_candidato) or "solic")
                    with open(os.path.join(pasta_dl, sname), "wb") as f:
                        f.write(blob)
                    # PEDIDO EM VARIAS FOLHAS: quando o pedido mais recente veio
                    # dividido (mesma data), TODAS as folhas sobem — senao a guia e
                    # anexada com metade do pedido. Caso JUCILENE (GTO 195371168).
                    # A folha extra vai como ESTA, sem ajuste de data: ela tem a
                    # mesma data da principal, que ja foi tratada acima.
                    _extras_idx = [i for i in (_det.get("idxs") or []) if i != idx]
                    for _ix in _extras_idx:
                        try:
                            _fn2, _mm2, _bl2, _sv2 = cands[_ix]
                            _sn2 = "SOLICITACAO_" + (
                                re.sub(r"[^A-Za-z0-9._-]+", "_", _fn2) or f"solic{_ix}")
                            with open(os.path.join(pasta_dl, _sn2), "wb") as f:
                                f.write(_bl2)
                        except Exception:
                            continue
                    if _extras_idx:
                        out["solicitacoes_extras"] = len(_extras_idx)
            break
        except Exception as e:
            out["erro"] = f"gemini: {str(e)[:120]}"
            if _gem_fatal(e):
                break            # crédito/cota/chave: repetir só faz perder tempo
            time.sleep(1.0 * (tent + 1))
    return out


# TTL da pasta de revisão. 90 dias, não 7: uma pendência fica aberta enquanto
# alguém não providencia o documento (laudo do radiologista, solicitação da
# clínica), e isso demora. Medido em 25/07: das 24 pendências abertas, a mais
# velha tinha 16 dias e a mediana 9 — com TTL de 7 dias, 16 delas (2/3) já
# teriam perdido os documentos que a usuária precisa ver para resolvê-las.
# Ajustável por env sem mexer no código.
_REVIEW_TTL_DIAS = int(os.environ.get("REVIEW_TTL_DIAS", "90"))


def _limpar_temporarios_antigos(review_root="/tmp/esteira_rev"):
    """Higiene de documento de paciente em disco (LGPD). Remove:
      - pastas de revisão mais velhas que REVIEW_TTL_DIAS (padrão 90);
      - sobras de execuções anteriores (_att_* / _esteira_*) com mais de 1 dia,
        que só existem se um processo morreu no meio.
    Silencioso de propósito: limpeza nunca pode derrubar a esteira."""
    agora = time.time()
    try:
        for nome in os.listdir(review_root):
            p = os.path.join(review_root, nome)
            try:
                if os.path.isdir(p) and (agora - os.path.getmtime(p)) > _REVIEW_TTL_DIAS * 86400:
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass
    try:
        raiz = tempfile.gettempdir()
        for nome in os.listdir(raiz):
            if not (nome.startswith("_att_") or nome.startswith("_esteira_")):
                continue
            p = os.path.join(raiz, nome)
            try:
                if os.path.isdir(p) and (agora - os.path.getmtime(p)) > 86400:
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass


def _anexos_portal_split(imgs):
    """Separa os anexos de /v1/gto/imagens em COPIAS DA GTO e DOCUMENTOS.

    imagemGTO=True marca a imagem ASSINADA da propria guia — o anexo com que
    toda guia nasce, e que uma RE-ASSINATURA pode DUPLICAR (casos PAULO SERGIO/
    WELLINGHTON/FABIO/PATRICK, 27/07: re-assinatura em lote as 21:30 criou a
    2ª copia da GTO e as 4 guias foram puladas como "ja documentadas" sem ter
    documento nenhum). Documentacao de verdade (laudo/solicitacao/entrega,
    nossa ou manual) chega com imagemGTO=False. Anexo SEM o flag conta como
    DOCUMENTO: na duvida a guia e tratada como ja documentada — o lado que
    NAO duplica anexo (o portal nao permite remover)."""
    copias, docs = [], []
    for i in imgs or []:
        if not isinstance(i, dict):
            continue
        if str(i.get("imagemGTO", "")).strip().lower() == "true":
            copias.append(i)
        else:
            docs.append(i)
    return copias, docs


def rodar_esteira(data, m_download=6, n_desc=3, k_leitura=5, log=None, gemini_key=None,
                  review_dir=None, k_attach=0, dry_run=True, conta=None, senha_portal=None):
    """Pipeline de até 4 estágios (descoberta -> download -> decisão -> anexação).
    conta = código da conta RedeUna (plano); usa o login + convênios/segmentos dela.
    gemini_key liga a decisão. k_attach>0 liga a ANEXAÇÃO (estágio 4): auto e
    justificativa são anexados; sem-solicitação e revisão NÃO (ficam avisados).
    dry_run=True só simula a anexação (loga o plano, não sobe nada)."""
    if log is None:
        log = lambda m: print(m, flush=True)
    plano = PLANOS.get(conta or "")
    # Conta informada mas desconhecida = erro de chamada. Antes caía no login PADRÃO
    # em silêncio e faturava na UNIDADE ERRADA. Falha explícito.
    if conta and not plano:
        raise ValueError(f"Conta/plano desconhecido: {conta!r}. "
                         f"Válidos: {sorted(PLANOS)}")
    _convenios = plano["convenios"] if plano else CONVENIOS
    _segmentos = plano["segmentos"] if plano else SEGMENTOS
    _odo_user = conta if (conta and plano) else None   # None -> usa ODONTOPREV_USER padrão
    t_glob = time.monotonic()
    with _gem_tokens_lock:               # consumo e estado são POR EXECUÇÃO
        _gem_tokens.update({"in": 0, "out": 0, "chamadas": 0})
        _gem_estado["fatal"] = None
        _campos_anexo["visto"] = False
        _campos_evento["visto"] = False

    def _t(m):
        log(f"[{time.monotonic() - t_glob:6.0f}s] {m}")

    def _odo_creds():
        """Login OdontoPrev: user = código da conta (plano); senha = por-código
        cadastrada na UI (senha_portal) ou, na falta, a ODONTOPREV_PASSWORD do env."""
        if senha_portal:
            du = None
            try:
                du, _ = get_credentials_odonto()
            except Exception:
                du = None
            return (_odo_user or du), senha_portal
        _du, pwd = get_credentials_odonto()
        return (_odo_user or _du), pwd

    gem = None
    if gemini_key:
        try:
            from google import genai
            gem = genai.Client(api_key=gemini_key)
            _t(f"Gemini 2.5 Flash ATIVO | pool de leitura K={k_leitura} (Tesseract fora)")
        except Exception as e:
            _t(f"Gemini indisponível ({str(e)[:80]}) — roda sem leitura")

    anexar_on = bool(gem) and k_attach > 0
    fila_pend = queue.Queue()
    fila_leit = queue.Queue()
    fila_anexar = queue.Queue()
    stop_desc = threading.Event()
    stop_dl = threading.Event()
    stop_dec = threading.Event()
    _lock = threading.Lock()
    resultados = []
    n_pend = {"n": 0}
    ativos_dl = {"n": 0, "pico": 0}
    ativos_le = {"n": 0, "pico": 0}
    ativos_an = {"n": 0, "pico": 0}

    # ---- ESTÁGIO 1: descoberta via API DIRETA (sem abrir popup) ----
    def _odonto_setup():
        """Login OdontoPrev (1 navegador): captura o Bearer token da sessão + lista
        os alvos. Depois disso a descoberta é HTTP puro (sem render de popup)."""
        user, pwd = _odo_creds()   # plano selecionado -> login = código da conta
        tok = {"v": None}
        alvos = []
        with sync_playwright() as pw:
            br, ctx, pg = login_odonto(pw, user, pwd)
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)

            def _grab(req):
                try:
                    if not tok["v"] and "credenciado.odontoprev.com.br" in req.url:
                        a = req.headers.get("authorization")
                        if a and a.lower().startswith("bearer"):
                            tok["v"] = a
                except Exception:
                    pass
            ctx.on("request", _grab)
            try:
                abrir_consultar_gtos(pg); consultar_periodo(pg, data)
                gtos = listar_gtos(pg)
                do_dia = [g for g in gtos if g.get("liberacao") == data]
                # ANTES: `or gtos` — se nenhuma linha batesse com a data, processava
                # TODAS as linhas da tela, que podem ser de outro dia (resquício da
                # consulta anterior, filtro que não aplicou, locale de data). Trocar
                # "não achei nada para este dia" por "então processa tudo" é o pior
                # default possível num sistema que ANEXA. Falha explícito.
                if gtos and not do_dia:
                    raise RuntimeError(
                        f"A consulta trouxe {len(gtos)} GTO(s), nenhuma com liberação "
                        f"em {data} — o filtro de período não foi aplicado. Nada "
                        f"processado (datas vistas: "
                        f"{sorted({g.get('liberacao') for g in gtos})[:6]}).")
                alvos = [g for g in do_dia if "REPASSE" in g["status"].upper()]
                if not tok["v"] and alvos:   # fallback: abre 1 GTO p/ disparar a API
                    try:
                        gp = abrir_gto(pg, alvos[0]["gto"], _refrescar=None)
                        gp.wait_for_timeout(1500)
                    except Exception:
                        pass
            finally:
                br.close()
        return tok["v"], alvos

    def descobridor_api(token, alvos):
        """Pra cada alvo, chama /v1/gto/imagens (nomes + contagem) e decide pendente.
        HTTP puro em paralelo (ThreadPool) -> sem popup, sem render, ~zero CPU."""
        from concurrent.futures import ThreadPoolExecutor
        sess = requests.Session()
        _pxy = _odo_requests_proxies()   # OdontoPrev via proxy residencial (PRORADIS fica direto)
        if _pxy:
            sess.proxies.update(_pxy)
        sess.headers.update({"Authorization": token or "", "User-Agent": "Mozilla/5.0",
                             "Origin": "https://credenciado.odontoprev.com.br",
                             "Referer": "https://credenciado.odontoprev.com.br/"})

        def _um(g):
            imgs = []          # usado tambem depois do try (download da GTO)
            # RETRY + captura do erro REAL. A leitura dos anexos era UMA tentativa e o
            # except engolia a excecao (cnt=-1) sem gravar o motivo — o log so dizia
            # "nao consegui LER", sem pista. O proxy residencial as vezes da timeout/
            # reset (caso ANA CELIA/WELLINGHTON, 27/07 run 261: as duas JA estavam
            # faturadas, so hiccuparam; a chamada volta 200 na tentativa seguinte).
            # Agora tenta 3x com backoff e, se ainda falhar, guarda o PORQUE (status
            # HTTP ou tipo+msg da excecao) para o log. Tambem trata status != 200 como
            # falha (antes virava cnt=0 -> guia lida como '0 anexos', mascarando um 500).
            _falha = None
            for _tent in range(3):
                try:
                    r = sess.get(f"{_ODO_API}/v1/gto/imagens"
                                 f"?numeroFicha={g['gto']}", timeout=25)
                    if r.status_code == 200:
                        imgs, _falha = (r.json() or []), None
                        break
                    _falha = f"HTTP {r.status_code} {r.text[:80]!r}"
                except Exception as e:
                    _falha = f"{type(e).__name__}: {str(e)[:100]}"
                if _tent < 2:
                    time.sleep(1.5 * (_tent + 1))
            if _falha:
                nomes, cnt = set(), -1
            else:
                nomes = {str(i.get("nomeArquivo", "")) for i in imgs}
                cnt = len(imgs)
                # DIAGNOSTICO: que campos o portal devolve por anexo (id/URL) — permite
                # comparar CONTEUDO em vez de nome. Sem isso a idempotencia depende do
                # nosso nome ser estavel.
                if imgs and isinstance(imgs[0], dict) and not _campos_anexo["visto"]:
                    _campos_anexo["visto"] = True
                    _t(f"[API] campos por anexo em /v1/gto/imagens: "
                       f"{sorted(imgs[0].keys())}")
                    _amostra = {k: (str(v)[:60] if v is not None else None)
                                for k, v in imgs[0].items()}
                    _t(f"[API] amostra: {_amostra}")
            # REGRA (31/07 — casos PAULO SERGIO/WELLINGHTON/FABIO/PATRICK, 27/07):
            # "ja documentada" NAO e contagem — e existir DOCUMENTO alem da guia.
            # Toda guia nasce com 1 anexo, a imagem assinada da propria GTO
            # (imagemGTO=True), mas uma RE-ASSINATURA cria uma 2ª copia da GTO e
            # a guia passa a ter 2 anexos SEM documentacao nenhuma — a regra
            # antiga (cnt >= 2 pula) marcou essas 4 guias como "ja documentadas"
            # e elas sairam do radar como faturadas. Agora so pula se ha anexo
            # com imagemGTO=False (laudo/solicitacao/entrega, nosso ou manual).
            # Anexar continua perigoso — o OdontoPrev NAO PERMITE REMOVER anexo
            # (casos CLAUDIA REGINA e VANESSA, 12 anexos) — entao o nº de copias
            # da GTO visto AGORA (n_gto_copias) vira o TETO do anexador:
            # qualquer anexo novo entre a descoberta e o upload bloqueia o envio.
            # Na duvida (cnt == -1, falha na consulta) tambem NAO segue.
            if cnt < 0:
                _t(f"[DESC] GTO {g['gto']}: nao consegui LER os anexos apos 3 "
                   f"tentativas [{_falha}] -> pula (nao arrisco duplicar; "
                   f"reprocesse o dia)")
                with _lock:
                    resultados.append({"gto": g["gto"], "nome": g["nome"],
                                       "status": "NAO_VERIFICADA"})
                return
            _copias, _docs = _anexos_portal_split(imgs)
            g["n_gto_copias"] = len(_copias)
            if _docs:
                # Os exames tambem para a guia PULADA. Sem isso a coluna "Exames" do
                # relatorio saia vazia justamente nas FATURADAS — "nenhum" em 27 de 27
                # no dia 24/07 — e a operadora nao tinha como conferir o que foi
                # faturado. E um GET a mais numa etapa que ja e so HTTP.
                _ex_ja = []
                try:
                    _rj = sess.get(f"{_ODO_API}/v1/gto/eventos/ficha"
                                   f"?numeroFicha={g['gto']}", timeout=20)
                    if _rj.status_code == 200:
                        _ex_ja = sorted(canon_exames(" ".join(
                            str(e.get("descricao") or "") for e in (_rj.json() or [])
                            if isinstance(e, dict))))
                except Exception:
                    _ex_ja = []
                _t(f"[DESC] GTO {g['gto']}: {cnt} anexos, {len(_docs)} documento(s) "
                   f"alem da GTO -> ja tem documentacao, pula | anexos: {sorted(nomes)}")
                with _lock:
                    resultados.append({"gto": g["gto"], "nome": g["nome"],
                                       "status": "JA_ANEXADO",
                                       "exames_portal": _ex_ja,
                                       "n_anexos": cnt, "n_docs": len(_docs),
                                       "anexos_no_portal": sorted(nomes)})
                return
            if cnt >= 2:
                _t(f"[DESC] GTO {g['gto']}: {cnt} anexos mas TODOS sao copia "
                   f"assinada da propria GTO (re-assinatura) -> SEM documentacao, "
                   f"entra na fila")
            g["nome_norm"] = normaliza_nome(g["nome"])
            # EXAMES DA GUIA direto do portal (fonte autoritativa). O PDF da GTO no
            # prontuário às vezes vem SEM a tabela de procedimentos — só os rótulos
            # dos campos — e aí gto_exames() volta vazio e a guia caía em "GTO
            # ilegível (sem exames de referência)". Este endpoint traz os eventos
            # DESTA ficha. ATENÇÃO: é /eventos/ficha; o /v1/gto/eventos (sem /ficha)
            # devolve o CATÁLOGO inteiro de procedimentos — usá-lo daria todos os
            # exames para qualquer guia.
            try:
                re_ = sess.get(f"{_ODO_API}/v1/gto/eventos/ficha?numeroFicha={g['gto']}",
                               timeout=20)
                evs = re_.json() if re_.status_code == 200 else []
                g["eventos_portal"] = [str(e.get("descricao") or "") for e in evs
                                       if isinstance(e, dict)]
                # DIAGNOSTICO (classe F, LUIZ/tomografia 28/07): hoje lemos SO o
                # campo 'descricao'. Guias RedeUna de tomografia cairam em GTO_ILEGIVEL
                # porque 'descricao' veio vazio — o exame pode estar em OUTRO campo do
                # evento. Loga 1x os campos e uma amostra pra saber QUAL campo ler antes
                # de mexer (o fix estrutural exige separar alvo-de-cobertura de alvo-de-
                # filtro-de-laudo; ate la nao alargamos o alvo). So observabilidade.
                if evs and isinstance(evs[0], dict) and not _campos_evento["visto"]:
                    _campos_evento["visto"] = True
                    _t(f"[API] campos por evento em /v1/gto/eventos/ficha: "
                       f"{sorted(evs[0].keys())}")
                    _t("[API] amostra evento: "
                       + str({k: (str(v)[:60] if v is not None else None)
                              for k, v in evs[0].items()}))
            except Exception:
                g["eventos_portal"] = []
            # A GUIA VEM DO PORTAL, NAO DO PRONTUARIO. Ate aqui, dentista e CRO da
            # guia so existiam se o PDF dela estivesse anexado no prontuario do
            # PRORADIS. Quando nao estava — caso JOSETE DIAS DE SANTANA, confirmado
            # pelo dono — o segundo sinal (carimbo do dentista) ficava CEGO: havia o
            # CRO lido no papel e nada contra o que comparar, e a guia virava
            # "nenhum documento esta no nome deste paciente".
            # Aqui a propria guia e baixada do RedeUna, onde ela SEMPRE existe: o
            # anexo marcado imagemGTO=True e, pela regra do dono, o unico anexo que
            # toda guia tem ao nascer. Baixar e barato (1 GET); a leitura por IA so
            # acontece depois, e so se o prontuario nao tiver a guia.
            try:
                # posicao (1-based) do anexo imagemGTO=True na lista — e o
                # `sequencial` que o acervo digital usa para servir o arquivo
                _seq = next((ix for ix, i in enumerate(imgs or [], 1)
                             if isinstance(i, dict)
                             and str(i.get("imagemGTO")).strip().lower() == "true"),
                            None)
                if _seq:
                    _b, _m = _baixar_anexo_portal(sess, g["gto"], _seq, _t)
                    if _b:
                        g["gto_portal_blob"], g["gto_portal_mime"] = _b, _m
            except Exception:
                pass
            with _lock:
                n_pend["n"] += 1
            _t(f"[DESC] >>> PENDENTE {g['gto']} {g['nome']} ({cnt} anexos) -> fila"
               + (f" | eventos: {g['eventos_portal']}" if g.get("eventos_portal") else ""))
            fila_pend.put(g)
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(_um, alvos))

    # ---- ESTÁGIO 2: download (PRORADIS) ----
    def baixador(wid, state, by_norm, tmp):
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = br.new_context(storage_state=state, locale="pt-BR", timezone_id="America/Sao_Paulo")
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)
            pg = ctx.new_page()
            pg.goto(f"{BASE}/admin_reports", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(800)
            while True:
                try:
                    g = fila_pend.get(timeout=2)
                except queue.Empty:
                    if stop_desc.is_set() and fila_pend.empty():
                        break
                    continue
                with _lock:
                    ativos_dl["n"] += 1; ativos_dl["pico"] = max(ativos_dl["pico"], ativos_dl["n"])
                try:
                    r = _baixa_um(pg, ctx, by_norm, g, tmp, data)
                except Exception as e:
                    r = {"gto": g["gto"], "nome": g["nome"], "status": "ERRO", "erro": str(e)[:120]}
                with _lock:
                    ativos_dl["n"] -= 1
                # inclui o NOME e o significado do status: SEM_MATCH/SEM_ARQUIVOS
                # sozinhos não dizem nada a quem lê o log pra decidir o que cobrar.
                _sig = {"SEM_MATCH": "paciente não localizado no PRORADIS neste dia",
                        "SEM_ARQUIVOS": "sem laudo/imagem para baixar (laudo não emitido?)",
                        "AMBIGUO": "mais de um paciente com esse nome — não dá pra saber qual",
                        "ERRO": r.get("erro", "")}.get(r["status"], "")
                _t(f"[DL{wid}] {g['gto']} {g['nome'][:22]} -> {r['status']}"
                   + (f" ({_sig})" if _sig else "")
                   + f" ({r.get('dt_dl', 0):.0f}s)")
                if gem is not None and r.get("status") == "BAIXADO" and r.get("_pac"):
                    fila_leit.put(r)         # entrega pro estágio de leitura
                else:
                    with _lock:
                        resultados.append(r)
            try:
                br.close()
            except Exception:
                pass

    # ---- ESTÁGIO 3: leitura (PRORADIS + Gemini) ----
    def leitor(wid, state):
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = br.new_context(storage_state=state, locale="pt-BR", timezone_id="America/Sao_Paulo")
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)
            pg = ctx.new_page()
            pg.goto(f"{BASE}/admin_reports", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(800)
            while True:
                try:
                    item = fila_leit.get(timeout=2)
                except queue.Empty:
                    if stop_dl.is_set() and fila_leit.empty():
                        break
                    continue
                with _lock:
                    ativos_le["n"] += 1; ativos_le["pico"] = max(ativos_le["pico"], ativos_le["n"])
                    em = _mem_mb()
                t0 = time.monotonic()
                try:
                    dec = _decidir(gem, pg, ctx, item["_pac"], item.get("_pasta"),
                                   review_dir=review_dir, gto=item["gto"],
                                   eventos_portal=item.get("eventos_portal"),
                                   gto_blob=item.get("gto_portal_blob"),
                                   gto_mime=item.get("gto_portal_mime") or "")
                except Exception as e:
                    dec = {"erro": str(e)[:100], "decisao": None, "anexos": 0,
                           "gto_exames": [], "plano_laudo_imgs": [], "plano_solicitacao": None}
                # Apaga os anexos do prontuário assim que a decisão sai: eles só
                # servem para decidir e são documento médico (LGPD).
                _ad = dec.pop("_att_dir", None)
                if _ad:
                    shutil.rmtree(_ad, ignore_errors=True)
                item["decisao"] = dec
                item["dt_decisao"] = time.monotonic() - t0
                with _lock:
                    ativos_le["n"] -= 1
                # GATE: LAUDO obrigatório para exames RADIOLÓGICOS. A justificativa
                # (campo 49) dispensa a SOLICITAÇÃO, nunca o laudo. EXCEÇÃO: GTO só de
                # MODELO/FOTOGRAFIA dispensa laudo (não são radiológicos). Sem laudo
                # onde é exigido, NÃO fatura -> pendência 'sem_laudo' na classificação.
                _tem_laudo = any(str(f).upper().startswith("LAUDO_")
                                 for f in dec.get("plano_laudo_imgs", []))
                _tem_solic_ou_justif = bool(dec.get("justificativa")) or bool(dec.get("plano_solicitacao"))
                _laudo_ok = _tem_laudo or bool(dec.get("dispensa_laudo"))
                anexa = _laudo_ok and _tem_solic_ou_justif
                if anexar_on and anexa:
                    fila_anexar.put(item)
                else:
                    with _lock:
                        resultados.append(item)
                d = dec.get("decisao") or {}
                if dec.get("justificativa"):
                    solic = "JUSTIFICATIVA (solic dispensada)"
                elif dec.get("plano_solicitacao"):
                    solic = f"SOLIC={dec['plano_solicitacao']}"
                else:
                    solic = "solic->REVISÃO"
                # MOTIVO no log: o DRY existe pra revisar decisões, e sem o motivo
                # ele dizia "REVISÃO" sem contar POR QUE — inútil pra quem precisa
                # agir. Inclui também o que falta pro gate (laudo/solicitação).
                _falta = []
                if not _laudo_ok:
                    _falta.append("LAUDO")
                if not _tem_solic_ou_justif:
                    _falta.append("SOLICITAÇÃO/JUSTIFICATIVA")
                _mot = d.get("motivo") or dec.get("erro") or ""
                _extra = ""
                if _falta:
                    _extra += f" | FALTA: {'+'.join(_falta)}"
                if _mot:
                    # NÃO truncar: o motivo é a justificativa que a operadora leva
                    # ao dentista. Cortado em 110 caracteres, saía pela metade
                    # ("...mas a GTO pede [do") e virava ilegível.
                    _extra += f" | MOTIVO: {_mot}"
                _gp = dec.get("gto_exames_desta") or dec.get("gto_exames")
                if _gp:
                    # esconde o token interno 'documentacao_completa'
                    _gp = [x for x in _gp if not str(x).startswith("documentacao_")]
                    _extra += f" | GTO pede: {_gp}"
                if item.get("data_exame_real"):
                    _t(f"[DATA] GTO {item['gto']} — exame encontrado em "
                       f"{item['data_exame_real']}, guia é de {data}")
                _fn = dec.get("funil") or {}
                if dec.get("descartados"):
                    _t(f"[DESCARTE] GTO {item['gto']} — anexo(s) NÃO lido(s): "
                       + " | ".join(dec["descartados"][:6]))
                if dec.get("convertidos"):
                    _t(f"[CONV] GTO {item['gto']} — convertido(s) p/ leitura: "
                       + ", ".join(dec["convertidos"][:6]))
                # nome COMPLETO (era cortado em 22 caracteres, inutilizando o log
                # para conferir paciente) + funil de anexos
                _t(f"[DEC{wid}] {item['gto']} {item['nome']} "
                   f"| anexos={_fn.get('prontuario', '?')}→cand={_fn.get('candidatos', '?')}"
                   f"{' desc=' + str(_fn['descartados']) if _fn.get('descartados') else ''}"
                   f" | laudo+img={len(dec.get('plano_laudo_imgs', []))} "
                   f"| {solic} | conf={d.get('confianca')} batem={d.get('exames_batem')}"
                   f"{_extra} ({item['dt_decisao']:.0f}s, mem={em:.0f}MB)")
            try:
                br.close()
            except Exception:
                pass

    # ---- ESTÁGIO 4: anexação (OdontoPrev) ----
    _anex_falhas = []   # motivos de morte dos anexadores (p/ explicar o que sobrou)

    def anexador(wid):
        """Um worker que morre NÃO pode levar junto as GTOs já aprovadas: o que
        ficar na fila é drenado depois do join (ver 'sobras' mais abaixo) e vira
        pendência com motivo, em vez de sumir do relatório em silêncio."""
        try:
            user, pwd = _odo_creds()   # plano selecionado -> login = código da conta
        except Exception as e:
            _anex_falhas.append(f"credenciais indisponíveis: {e}")
            _t(f"[ANEX{wid}] credenciais indisponíveis: {str(e)[:80]}")
            return
        try:
            pwctx = sync_playwright().start()
        except Exception as e:
            _anex_falhas.append(f"falha ao iniciar navegador: {e}")
            _t(f"[ANEX{wid}] falha ao iniciar navegador: {str(e)[:80]}")
            return
        try:
            try:
                br, ctx, pg = login_odonto(pwctx, user, pwd)
            except Exception as e:
                _anex_falhas.append(f"login OdontoPrev falhou: {e}")
                _t(f"[ANEX{wid}] login OdontoPrev falhou: {str(e)[:80]}")
                return
            ctx.set_default_timeout(45000); ctx.set_default_navigation_timeout(60000)
            try:
                abrir_consultar_gtos(pg); consultar_periodo(pg, data)
            except Exception as e:
                _t(f"[ANEX{wid}] consulta inicial falhou: {str(e)[:80]}")
            while True:
                try:
                    item = fila_anexar.get(timeout=2)
                except queue.Empty:
                    if stop_dec.is_set() and fila_anexar.empty():
                        break
                    continue
                pasta = item.get("_pasta")
                arquivos, excluidos, exames_fora = _filtrar_arquivos_da_gto(
                    pasta, item.get("decisao") or {}, item.get("extras_acc"),
                    item.get("convenio_acc"))
                if excluidos:
                    item["laudos_excluidos"] = excluidos
                    item["exames_particulares"] = exames_fora
                    # LAUDO PRONTO SEM GUIA: dos excluídos, os de PROCEDÊNCIA
                    # (accession fora do convênio) — vira AVISO ao dono (não pendência).
                    item["laudos_sem_guia"] = _laudos_sem_guia(excluidos,
                                                               item.get("extras_acc"))
                    _t(f"[ANEX{wid}] GTO {item['gto']} EXAMES MISTOS — não anexados "
                       f"(fora da guia): {exames_fora} | {excluidos}")
                nomes = [os.path.basename(a) for a in arquivos]
                item["arquivos_anexados"] = nomes
                # GUARDA FINAL — o filtro acima pode remover TODOS os laudos (exame
                # particular, ou canon que não reconheceu o exame). Se sobrou guia sem
                # laudo, NÃO anexa: o gate lá atrás autorizou porque havia laudo na
                # pasta, e subir só a solicitação (ou zero arquivo) registraria como
                # FATURADA uma guia sem o documento obrigatório. Vira pendência.
                _dec_it = item.get("decisao") or {}
                _laudo_no_plano = any(n.upper().startswith("LAUDO_") for n in nomes)
                if not _laudo_no_plano and not _dec_it.get("dispensa_laudo"):
                    item["anexado"] = "ERRO"
                    # A MENSAGEM PRECISA DIZER *QUAIS* EXAMES. "Conferir se o exame e
                    # do convenio" escondia dois casos opostos e a pessoa nao tinha
                    # como saber em qual estava:
                    #   (a) o exame era particular mesmo -> nada a fazer;
                    #   (b) o exame E da guia e nos nao reconhecemos o nome -> estamos
                    #       perdendo faturamento por falha nossa.
                    # Caso ELIENE LIMA DE OLIVEIRA LOPEZ, 25/07: a guia pedia
                    # periapical e os unicos laudos eram de panoramica e
                    # telerradiografia, de uma documentacao do MESMO dia (acessao
                    # 40336804). Era (a) — mas so deu para saber lendo o log.
                    _ex_guia = lista_amigavel(_dec_it.get("gto_exames")
                                              or item.get("exames_gto") or [])
                    _ex_fora = lista_amigavel(exames_fora or [])
                    if excluidos and _ex_fora:
                        item["anexar_erro"] = (
                            f"a guia pede {_ex_guia or 'exames que não consegui ler'}, "
                            f"mas os laudos encontrados eram de {_ex_fora} — de outro "
                            f"exame do mesmo dia. O QUE FAZER: se esses laudos SÃO "
                            f"desta guia, o nome do exame está diferente do que "
                            f"reconhecemos (falha nossa); se forem de exame "
                            f"particular, não há o que faturar.")
                    elif excluidos:
                        item["anexar_erro"] = (
                            f"a guia pede {_ex_guia or '(exames ilegíveis)'} e todos os "
                            f"laudos do paciente foram excluídos por não pertencerem a "
                            f"ela — conferir se o exame é do convênio")
                    else:
                        item["anexar_erro"] = (
                            f"não há nenhum laudo para anexar. A guia pede "
                            f"{_ex_guia or '(exames ilegíveis)'} — cobrar a emissão "
                            f"do laudo com o radiologista.")
                    _t(f"[ANEX{wid}] GTO {item['gto']} NÃO ANEXADA: sem laudo no plano "
                       f"(excluídos: {excluidos or '—'})")
                    with _lock:
                        resultados.append(item)
                    continue
                with _lock:
                    ativos_an["n"] += 1; ativos_an["pico"] = max(ativos_an["pico"], ativos_an["n"])
                if dry_run:
                    item["anexado"] = "DRY"
                    _t(f"[ANEX{wid}] [DRY] GTO {item['gto']} ANEXARIA {len(arquivos)}: {nomes}")
                else:
                    try:
                        gp = abrir_gto(pg, item["gto"])
                        # ÚLTIMA GUARDA antes do único ponto de escrita irreversível:
                        # confere que a guia aberta é do paciente esperado. Só bloqueia
                        # quando lê um nome DIFERENTE — se não conseguir ler (campo
                        # vazio/layout novo), segue, para não travar o faturamento.
                        try:
                            _pop = ler_dados_gto(gp).get("nome") or ""
                        except Exception:
                            _pop = ""
                        # exige um nome DE VERDADE (>=2 tokens) antes de usar isto pra
                        # bloquear: uma captura truncada do regex nao pode cancelar
                        # upload legitimo. Na duvida sobre a leitura, deixa passar.
                        _pop_ok = len([t for t in normaliza_nome(_pop).split()
                                       if len(t) > 1]) >= 2
                        if _pop_ok and not _nomes_compat(_pop, item["nome"]):
                            item["anexado"] = "ERRO"
                            item["anexar_erro"] = (f"guia aberta é de {_pop!r}, esperado "
                                                   f"{item['nome']!r} — upload cancelado")
                            _t(f"[ANEX{wid}] GTO {item['gto']} CANCELADO: popup mostra "
                               f"{_pop!r}, esperado {item['nome']!r}")
                            try:
                                gp.close()
                            except Exception:
                                pass
                            with _lock:
                                ativos_an["n"] -= 1
                                resultados.append(item)
                            continue
                        # ULTIMA TRAVA antes da escrita IRREVERSIVEL. O OdontoPrev
                        # nao permite remover anexo: duplicar e dano permanente. A
                        # contagem da descoberta pode estar velha (outra execucao, ou
                        # alguem anexando a mao no meio) — reconfere na guia ABERTA.
                        # O teto NAO e fixo em 1: guia RE-ASSINADA nasce com 2+
                        # copias da propria GTO (n_gto_copias, contado na descoberta
                        # pelo flag imagemGTO da API). Qualquer anexo ALEM das
                        # copias vistas la — documento ou mais uma copia — bloqueia.
                        _lim = item.get("n_gto_copias")
                        _lim = _lim if isinstance(_lim, int) and _lim >= 1 else 1
                        try:
                            _dom_n = _anexos_count(gp)
                        except Exception:
                            _dom_n = -1
                        # DOM falhou (o regex 'total de anexos)' nao casou / render
                        # diferente)? RECONTA pela API autoritativa /v1/gto/imagens, a
                        # MESMA fonte que a descoberta confia — casos JOSE/LEONARDO/
                        # SUELEM/RAFAEL/MARIA SOPHIA (28/07 run 263): doc OK, 6 retries
                        # do DOM e mesmo assim -1. Guardrail em _reconta_anexos: se a
                        # API TAMBEM falhar, _n_agora fica -1 e a trava abaixo bloqueia
                        # (nada enviado). upload_arquivos ainda RE-checa pelo DOM: duas
                        # fontes independentes antes da escrita irreversivel.
                        _n_agora, _fonte_cont, _cont_err = _reconta_anexos(
                            _dom_n, lambda: _anexos_count_api(token, item["gto"], _t))
                        if _fonte_cont == "API":
                            _t(f"[ANEX{wid}] GTO {item['gto']}: DOM nao leu os anexos; "
                               f"recontei pela API autoritativa = {_n_agora}")
                        if _n_agora is None or _n_agora < 0 or _n_agora > _lim:
                            item["anexado"] = "ERRO"
                            item["anexar_erro"] = (
                                f"guia ja tem {_n_agora} anexo(s), acima da(s) "
                                f"{_lim} copia(s) da GTO vistas na descoberta — nada "
                                f"foi enviado (algo mudou no meio; o portal nao "
                                f"permite remover anexo, entao duplicar seria dano "
                                f"permanente)"
                                if isinstance(_n_agora, int) and _n_agora > _lim else
                                "nao consegui ler quantos anexos a guia ja tem "
                                f"(DOM e API falharam{': ' + _cont_err if _cont_err else ''})"
                                " — nada foi enviado, por seguranca")
                            _t(f"[ANEX{wid}] GTO {item['gto']} BLOQUEADO: "
                               f"{_n_agora} anexo(s) na guia (teto {_lim})"
                               + (f" [{_cont_err}]" if _cont_err else ""))
                            try:
                                gp.close()
                            except Exception:
                                pass
                            with _lock:
                                ativos_an["n"] -= 1
                                resultados.append(item)
                            continue
                        # contar_fallback: se o DOM do ponto de escrita tambem nao
                        # renderizar o "total de anexos)", upload_arquivos reconta+
                        # relista pela API autoritativa (a mesma da descoberta) —
                        # senao as guias de DOM quebrado ficam presas la mesmo com a
                        # trava ja liberada. Se a API tambem falhar -> antes<0 ->
                        # upload bloqueia (nada enviado).
                        res = upload_arquivos(
                            gp, arquivos, max_antes=_lim,
                            contar_fallback=lambda: _anexos_via_api(token, item["gto"])[:2])
                        try:
                            gp.close()
                        except Exception:
                            pass
                        item["anexado"] = "OK" if res.get("ok") else "FALHOU"
                        item["upload"] = {k: res.get(k) for k in ("anexos_antes", "anexos_depois", "enviados", "ja_anexados")}
                        _t(f"[ANEX{wid}] GTO {item['gto']} -> {item['anexado']} "
                           f"({len(res.get('enviados', []))} enviados, {len(res.get('ja_anexados', []))} já tinha)")
                    except Exception as e:
                        item["anexado"] = "ERRO"; item["anexar_erro"] = str(e)[:120]
                        _t(f"[ANEX{wid}] GTO {item['gto']} ERRO {str(e)[:90]}")
                with _lock:
                    ativos_an["n"] -= 1
                    resultados.append(item)
            try:
                br.close()
            except Exception:
                pass
        except Exception as e:
            _anex_falhas.append(f"anexador interrompido: {e}")
            _t(f"[ANEX{wid}] worker morreu: {str(e)[:90]}")
        finally:
            try:
                pwctx.stop()
            except Exception:
                pass

    # ---- 1) SETUP: PRORADIS (by_norm) e OdontoPrev (token+alvos) em paralelo ----
    _t(f"=== PIPELINE {data} | dl={m_download} leit={k_leitura if gem else 0} (descoberta via API) ===")
    setup = {}

    def _prorad_setup():
        try:
            email, password = get_credentials()
            with sync_playwright() as pw0:
                br0, ctx0, pg0 = _login_playwright(pw0, email, password)
                ctx0.set_default_timeout(45000); ctx0.set_default_navigation_timeout(60000)
                df = _get_relatorio_analitico(pg0, _convenios, _segmentos, data)
                setup["by_norm"] = _build_by_norm(df)
                setup["state"] = ctx0.storage_state()
                br0.close()
        except Exception as e:
            setup["err_prorad"] = e

    def _odo_setup():
        try:
            setup["token"], setup["alvos"] = _odonto_setup()
        except Exception as e:
            setup["err_odo"] = e

    _ts = [threading.Thread(target=_prorad_setup), threading.Thread(target=_odo_setup)]
    for t in _ts:
        t.start()
    for t in _ts:
        t.join()
    # Login/consulta que falha ABORTA a execução (não segue como "0 faturados/sucesso").
    if setup.get("err_odo") is not None:
        _cod = _odo_user or "(padrão)"
        _det_odo = str(setup["err_odo"])
        _low = _det_odo.lower()
        # PROXY != senha. ERR_PROXY_AUTH_UNSUPPORTED / net::ERR_PROXY* vem do proxy
        # residencial (o OdontoPrev bloqueia IP de datacenter, entao o acesso passa
        # por proxy). Mandar "cadastre a senha do portal" aqui manda a operacao
        # para o lugar errado — a senha esta certa, quem falhou foi o proxy (saldo/
        # dados acabaram, credencial trocou, ou instabilidade do provedor).
        if "proxy" in _low or "err_proxy" in _low or "tunnel" in _low:
            raise RuntimeError(
                f"Não foi possível conectar ao OdontoPrev pelo proxy (código {_cod}) "
                f"— NÃO é a senha do portal. É o proxy residencial que dá acesso ao "
                f"OdontoPrev que falhou (saldo/dados acabaram, credencial mudou, ou "
                f"instabilidade do provedor). O QUE FAZER: conferir a conta do proxy "
                f"(ODONTO_PROXY_URL) e o saldo/dados; depois tentar de novo. "
                f"Detalhe técnico: {_det_odo[:140]}")
        raise RuntimeError(
            f"Login no RedeUna/OdontoPrev falhou para o código {_cod} — "
            f"verifique/cadastre a senha do portal. Detalhe: {_det_odo[:140]}")
    if setup.get("err_prorad") is not None:
        _ep = str(setup["err_prorad"])
        if "sem linhas" in _ep.lower() or "vazi" in _ep.lower():
            # login OK, mas o relatório analítico veio sem dados -> laudos não saíram
            raise RuntimeError(
                f"O PRORADIS não retornou laudos para {data} nesta unidade — "
                f"os exames podem não ter sido laudados ainda, ou o dia/unidade está "
                f"incorreto. Nada a faturar.")
        raise RuntimeError(
            f"Login/consulta no PRORADIS falhou. Detalhe: {_ep[:140]}")
    by_norm, state = setup.get("by_norm", {}), setup.get("state")
    token, alvos = setup.get("token"), setup.get("alvos", [])
    _t(f"PRORADIS by_norm={len(by_norm)} | OdontoPrev token={'ok' if token else 'FALHOU'} "
       f"| {len(alvos)} alvo(s)")
    # Sem token, TODA chamada da descoberta volta 401 -> imgs=[] -> cnt=0 ->
    # tem_laudo=False: cada GTO do dia é classificada como pendente e re-baixada do
    # PRORADIS, sem os eventos da ficha. Não falhava — refazia trabalho em silêncio
    # e decidia com menos informação. Aqui (fora das threads, onde o raise chega ao
    # chamador) a execução para com motivo.
    if alvos and not token:
        raise RuntimeError(
            "Token do OdontoPrev não foi capturado no login — a descoberta não "
            "consegue ler os anexos das GTOs. Nada foi processado; tente de novo "
            "em alguns minutos.")
    tmp = tempfile.mkdtemp(prefix="_esteira_")
    _limpar_temporarios_antigos()   # varre sobras antigas antes de gerar as novas

    # ---- 2) lança os pools (descoberta-API + download + decisão + anexação) ----
    tds = [threading.Thread(target=descobridor_api, args=(token, alvos), daemon=True)]
    tws = [threading.Thread(target=baixador, args=(i, state, by_norm, tmp), daemon=True)
           for i in range(1, m_download + 1)]
    tls = ([threading.Thread(target=leitor, args=(i, state), daemon=True)
            for i in range(1, k_leitura + 1)] if gem else [])
    tas = ([threading.Thread(target=anexador, args=(i,), daemon=True)
            for i in range(1, k_attach + 1)] if anexar_on else [])
    if anexar_on:
        _t(f"ANEXAÇÃO {'(DRY-RUN)' if dry_run else 'REAL'} ligada | K_attach={k_attach}")
    t_ini = time.monotonic()
    for t in tds + tws + tls + tas:
        t.start()
    for t in tds:
        t.join()
    t_desc = time.monotonic() - t_ini
    stop_desc.set()
    for t in tws:
        t.join()
    t_dl = time.monotonic() - t_ini
    stop_dl.set()
    for t in tls:
        t.join()
    t_dec = time.monotonic() - t_ini
    stop_dec.set()
    for t in tas:
        t.join()
    # SOBRAS: se todos os anexadores morreram (login bloqueado, navegador não subiu),
    # as GTOs JÁ APROVADAS ficariam presas na fila e sumiriam do relatório — sem
    # faturar e sem virar pendência. Aqui elas voltam com motivo explícito.
    if anexar_on:
        _sobrou = 0
        while True:
            try:
                _it = fila_anexar.get_nowait()
            except queue.Empty:
                break
            _it["anexado"] = "ERRO"
            _it["anexar_erro"] = ("anexação não executada: "
                                  + ("; ".join(_anex_falhas)[:100] if _anex_falhas
                                     else "worker encerrou antes de processar"))
            with _lock:
                resultados.append(_it)
            _sobrou += 1
        if _sobrou:
            _t(f"[ANEX] {_sobrou} GTO(s) NÃO anexada(s) e devolvida(s) como pendência "
               f"(motivo: {'; '.join(_anex_falhas)[:80] or 'fila não drenada'})")
    total = time.monotonic() - t_ini

    baixados = [r for r in resultados if r["status"] == "BAIXADO"]
    com_solic = [r for r in baixados if (r.get("decisao") or {}).get("plano_solicitacao")]
    com_justif = [r for r in baixados if (r.get("decisao") or {}).get("justificativa")]
    # painel das decisões (pro dry-run que você revisa)
    decisoes = []
    _outros_res = [r for r in resultados
                   if r["status"] in ("SEM_MATCH", "AMBIGUO", "SEM_ARQUIVOS",
                                      "JA_ANEXADO", "NAO_VERIFICADA", "ERRO")]

    for r in baixados + _outros_res:
        if r.get("status") == "JA_ANEXADO":
            _an = r.get("anexos_no_portal") or []
            # n_anexos e a CONTAGEM real da API — len(_an) e um SET de nomes e
            # mentia quando dois anexos tinham o mesmo nome (2x img_ASSINADA.png
            # dizia "ja tinha 1 anexo(s)" — foi assim que o dono pegou o bug da
            # re-assinatura em 31/07)
            _na = r.get("n_anexos") or (len(_an) or 2)
            _nd = r.get("n_docs") or 0
            decisoes.append({
                "gto": r["gto"], "paciente": r["nome"],
                # categoria PROPRIA: antes vinha como "auto"/anexado OK, igual a uma
                # guia que NOS faturamos. O relatorio dizia "faturada" para uma guia
                # em que o robo nao encostou, e a operadora nao tinha como saber a
                # diferenca. Nada pode ser silencioso.
                "categoria": "ja_anexada",
                "anexado": "OK", "laudo_imgs": [], "solicitacao": None,
                "anexar_solic": False, "justificativa": None,
                "gto_exames": r.get("exames_portal") or [],
                "candidatos": [], "solic_idx": None,
                "gemini": {"motivo": (
                    f"NAO FOI PRECISO FATURAR: a guia ja tinha {_na} anexo(s) "
                    + (f"— {_nd} documento(s) alem da propria GTO — "
                       if _nd else "")
                    + f"quando o robo chegou: a documentacao ja havia sido anexada, "
                    f"por outra execucao ou a mao. O robo NAO enviou nada: o portal "
                    f"nao permite remover anexo, e duplicar seria irreversivel."
                    + (f" Anexos na guia: {', '.join(_an)}." if _an else ""))},
                "erro": None, "arquivos_anexados": _an,
            })
            continue
        if r.get("status") == "NAO_VERIFICADA":
            decisoes.append({
                "gto": r["gto"], "paciente": r["nome"], "categoria": "erro",
                "anexado": None, "laudo_imgs": [], "solicitacao": None,
                "anexar_solic": False, "justificativa": None, "gto_exames": [],
                "candidatos": [], "solic_idx": None,
                "gemini": {"motivo": (
                    "NAO FOI PROCESSADA porque o sistema nao conseguiu consultar "
                    "quantos anexos a guia ja tem. Sem essa informacao nao da para "
                    "anexar com seguranca — o portal nao permite remover anexo, e "
                    "duplicar seria irreversivel. O QUE FAZER: reprocessar o dia. "
                    "(Falha nossa, nao da clinica.)")},
                "erro": "nao foi possivel ler os anexos da guia",
            })
            continue

        # Status em que o prontuário NEM FOI ABERTO: o motivo tem que dizer a
        # VERDADE (nada de "campo 49 vazio" — isso acusaria a clínica sem termos
        # olhado o campo 49). Caso MARTA 18/07.
        _st = r.get("status")
        if _st in ("SEM_MATCH", "SEM_ARQUIVOS", "AMBIGUO", "ERRO"):
            _mot_st = {
                "SEM_MATCH": (
                    f"NÃO FATUROU porque o paciente da guia não foi encontrado no "
                    f"PRORADIS. O sistema procurou pelo nome que está na guia em {data} "
                    f"e também nos {_JANELA_DIAS} dias antes e depois, e não achou exame "
                    f"nenhum. Como a janela de datas já foi varrida, a causa mais "
                    f"provável é o NOME estar escrito diferente nos dois sistemas "
                    f"(abreviação, nome de casada, erro de digitação) ou o exame não "
                    f"ter sido registrado. "
                    f"O QUE FAZER: procurar o paciente no PRORADIS pelo primeiro nome e "
                    f"conferir se o cadastro bate com o da guia."),
                "SEM_ARQUIVOS": (
                    f"NÃO FATUROU porque o exame existe no PRORADIS em {data}, mas não "
                    f"há laudo nem imagem para baixar. O exame foi registrado e ainda "
                    f"não tem entregável. "
                    f"O QUE FAZER: cobrar a emissão do laudo com o radiologista."),
                "AMBIGUO": (
                    (f"NÃO FATUROU porque este paciente tem exame em MAIS DE UM dia "
                     f"próximo à guia ({', '.join(r.get('dias_com_exame') or [])}) e o "
                     f"sistema não tem como saber qual exame pertence a esta guia — pode "
                     f"haver outra guia para o outro dia. "
                     f"O QUE FAZER: conferir qual data corresponde a esta guia e anexar "
                     f"manualmente.")
                    if r.get("dias_com_exame") else
                    ("NÃO FATUROU porque há mais de um paciente com esse nome no PRORADIS "
                     "no mesmo dia, e o sistema não tem como saber qual é o certo. Anexar "
                     "o exame da pessoa errada é pior do que não anexar. "
                     "O QUE FAZER: abrir os dois cadastros, identificar o paciente da guia "
                     "e anexar manualmente.")),
                "ERRO": (
                    "NÃO FATUROU por falha técnica nossa, não da clínica nem do "
                    "radiologista — o processamento desta guia foi interrompido. "
                    f"O QUE FAZER: reprocessar o dia. Detalhe técnico: "
                    f"{str(r.get('erro') or '')[:200]}"),
            }[_st]
            _cat_st = {"SEM_MATCH": "sem_exame", "SEM_ARQUIVOS": "sem_exame",
                       "AMBIGUO": "revisao", "ERRO": "erro"}[_st]
            decisoes.append({
                "gto": r["gto"], "paciente": r["nome"], "categoria": _cat_st,
                "anexado": r.get("anexado"), "laudo_imgs": [], "solicitacao": None,
                "anexar_solic": False, "justificativa": None, "gto_exames": [],
                "candidatos": [], "solic_idx": None,
                "gemini": {"motivo": _mot_st}, "erro": r.get("erro"),
            })
            continue

        dec = r.get("decisao") or {}
        d = dec.get("decisao") or {}
        _tem_laudo = any(str(f).upper().startswith("LAUDO_")
                         for f in dec.get("plano_laudo_imgs", []))
        # LAUDO obrigatorio p/ exames RADIOLOGICOS (mesmo com justificativa). Excecao:
        # GTO so de MODELO/FOTOGRAFIA dispensa laudo. Justificativa dispensa so a solic.
        _laudo_falta = not _tem_laudo and not dec.get("dispensa_laudo")
        if _laudo_falta and (dec.get("justificativa") or dec.get("plano_solicitacao")):
            cat = "sem_laudo"          # tem solic/justif mas falta laudo -> pendência
        elif dec.get("justificativa"):
            cat = "justificativa"      # laudo ok (ou dispensado)
        elif dec.get("plano_solicitacao"):
            cat = "auto"               # laudo ok (ou dispensado)
        elif dec.get("decisao") is None:
            cat = "erro"               # _decidir falhou (Gemini/anexos) — NÃO é culpa da clínica
            _e = str(dec.get("erro") or "")
            if "429" in _e or "RESOURCE_EXHAUSTED" in _e or "quota" in _e.lower():
                dec["erro"] = ("NÃO FATUROU porque a leitura automática ficou "
                               "indisponível: os créditos da API de leitura acabaram. "
                               "Nenhuma guia é lida enquanto isso. O QUE FAZER: "
                               "recarregar os créditos e reprocessar o dia. "
                               "(Falha nossa — o documento do paciente pode estar "
                               "perfeito.) Detalhe: " + _e[:120])
            elif _e:
                dec["erro"] = ("NÃO FATUROU por falha técnica na leitura dos documentos "
                               "— não é problema do documento nem da clínica. "
                               "O QUE FAZER: reprocessar o dia. Detalhe: " + _e[:160])
        elif d.get("indice_solicitacao") is None:
            cat = "sem_solicitacao"
        else:
            cat = "revisao"
        decisoes.append({
            "gto": r["gto"], "paciente": r["nome"], "categoria": cat,
            "data_exame_real": r.get("data_exame_real"),
            "anexado": r.get("anexado"),
            "anexar_erro": r.get("anexar_erro"),   # p/ o motivo da pendência ser o REAL
            "laudo_imgs": dec.get("plano_laudo_imgs", []),
            # EVIDENCIA durável: o que foi anexado de fato, o que foi retirado do
            # plano e o funil de anexos do prontuário.
            "arquivos_anexados": r.get("arquivos_anexados") or [],
            "laudos_excluidos": r.get("laudos_excluidos") or [],
            "funil": dec.get("funil") or {},
            "solicitacao": dec.get("plano_solicitacao"),
            "anexar_solic": bool(dec.get("plano_solicitacao")),
            "justificativa": dec.get("justificativa"),
            "gto_exames": dec.get("gto_exames", []),
            "candidatos": dec.get("candidatos", []),
            "solic_idx": dec.get("solic_idx"),
            "gemini": {k: d.get(k) for k in ("tipo", "legivel", "paciente_lido",
                       "exames_lidos", "exames_batem", "confianca", "anexar", "motivo")},
            "nomes_lidos": d.get("nomes_lidos"),
            "erro": dec.get("erro"),
        })
    n_rev = len(baixados) - len(com_solic) - len(com_justif)
    anx = [r.get("anexado") for r in baixados if r.get("anexado")]
    resumo = {
        "data": data, "conta": conta, "n_desc": n_desc, "m_download": m_download,
        "k_leitura": k_leitura if gem else 0, "gemini": bool(gem),
        "pendentes": n_pend["n"], "baixados": len(baixados),
        "outros": len(resultados) - len(baixados),
        "solic_auto": len(com_solic), "justificativa": len(com_justif), "revisao": n_rev,
        "anexar_on": anexar_on, "dry_run": dry_run,
        "anexado_ok": anx.count("OK"), "anexado_dry": anx.count("DRY"),
        "anexado_falhou": anx.count("FALHOU") + anx.count("ERRO"),
        "nao_faturadas": n_rev,
        "pico_download": ativos_dl["pico"], "pico_leitura": ativos_le["pico"],
        "pico_anexacao": ativos_an["pico"],
        "tempo_descoberta": round(t_desc), "tempo_ate_download": round(t_dl),
        "tempo_total": round(total), "decisoes": decisoes, "resultados": resultados,
        "gemini_tokens": dict(_gem_tokens),
        "gemini_chamadas_por_gto": (round(_gem_tokens["chamadas"] / len(baixados), 2)
                                    if baixados else 0),
    }
    _t(f"RESUMO: {resumo['baixados']}/{resumo['pendentes']} baixados | "
       f"{resumo['solic_auto']} solic-auto / {resumo['justificativa']} c-justificativa / "
       f"{resumo['revisao']} revisão | anexados ok={resumo['anexado_ok']} dry={resumo['anexado_dry']} "
       f"falhou={resumo['anexado_falhou']} | TOTAL={resumo['tempo_total']}s")
    if _gem_estado["fatal"]:
        _t(f"[GEMINI] *** LEITURA INDISPONÍVEL a partir de certo ponto desta "
           f"execução — as guias seguintes NÃO foram lidas. Motivo: "
           f"{_gem_estado['fatal']}. Recarregue os créditos e reprocesse o dia. ***")
    _t(f"[GEMINI] {_gem_tokens['chamadas']} chamadas "
       f"({resumo['gemini_chamadas_por_gto']}/GTO) | tokens in={_gem_tokens['in']:,} "
       f"out={_gem_tokens['out']:,} | thinking_budget={_GEM_THINKING}")
    # Laudos e imagens do dia já foram anexados — apaga a pasta da execução.
    # (Só nomes de arquivo seguem no resumo; ninguém lê o conteúdo depois daqui.)
    try:
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass
    return resumo
