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
    if len(comuns) < 2:
        return False
    if len(sa) <= len(sb):
        menor, maior, lista_maior = sa, sb, tb
    else:
        menor, maior, lista_maior = sb, sa, ta
    se_falta = menor - comuns
    if not se_falta:
        return True                      # menor totalmente contido no maior
    if len(se_falta) > 1:
        return False                     # 2+ tokens divergentes -> outra pessoa
    tok = next(iter(se_falta))
    if _erro_de_grafia(tok, maior - comuns):
        return True
    # NOME COMPOSTO grudado num sistema e separado no outro (VERALUCIA / VERA LUCIA).
    # Usa a lista ORDENADA: só concatena tokens que estão lado a lado no nome.
    return _casa_por_concatenacao(tok, lista_maior)


def alvo_cobertura(gto_ex_desta, exames_portal, gto_ex_uniao):
    """Conjunto de exames que a solicitação precisa COBRIR, por precedência:

      1) a GTO DESTA guia (número conferido no PDF, ou lida por imagem);
      2) os eventos DESTA ficha no portal (a operadora dizendo o que autorizou);
      3) a união das GTOs achadas no prontuário — ÚLTIMO recurso.

    (3) inclui GTOs de OUTRAS visitas do mesmo paciente. Usá-la como alvo cobra da
    solicitação exames que esta guia nunca pediu, e reprova documentação correta.
    Ela só existe como rede: sem identificar a guia, é mais seguro exigir demais
    (pendência) do que de menos (anexar errado)."""
    return set(gto_ex_desta or ()) or set(exames_portal or ()) or set(gto_ex_uniao or ())


def _escolher_solicitacao(leituras, nome_gto, gto_ex, n_cands):
    """NÍVEL 2 — o CÓDIGO escolhe a solicitação certa entre as leituras que o Gemini
    transcreveu (uma por anexo). Determinístico: tipo solicitação, legível, paciente
    compatível (tokens) e exames que COBREM os da GTO. Desempate: mais exames em
    comum, depois o mais recente (idx menor). Retorna (idx, leitura, motivo_ou_None)."""
    melhor = None
    algum_pac = False
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
        if not _nomes_compat(a.get("paciente_lido") or "", nome_gto):
            continue
        algum_pac = True
        # expande SÓ o lado da solicitação: quem pede os componentes (panorâmica +
        # telerradiografia + ...) está pedindo uma documentação. A recíproca não vale.
        ex = expande_documentacao(
            canon_exames(" ".join(str(e) for e in (a.get("exames_lidos") or []))))
        if not (gto_ex and gto_ex.issubset(ex)):
            continue
        score = (len(gto_ex & ex), -ai)
        if melhor is None or score > melhor[0]:
            melhor = (score, ai, a)
    if melhor is not None:
        return melhor[1], melhor[2], None
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
MANUSCRITO (letra cursiva). Transcreva LITERALMENTE o que está escrito no campo dos
exames/procedimentos pedidos — palavra por palavra, como aparece no papel, mesmo que
esteja abreviado, com grafia imperfeita ou você não reconheça o termo.

NÃO interprete, NÃO complete, NÃO deduza e NÃO acrescente nada que não esteja escrito.
Se não conseguir ler um trecho, omita-o em vez de adivinhar.

Responda APENAS JSON (sem markdown): {"exames": ["...", "..."]}"""


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
 "campo_49": "<texto do campo '49 - Observação / Justificativa'; "" se vazio>"}

Regras:
- "e_gto" só é true se o documento for mesmo a guia do convênio (tem numeração de
  campos tipo "2 -", "7 -", "49 -", tabela de procedimentos). Receituário, pedido
  de exame, RG, laudo ou nota fiscal => false.
- Transcreva LITERALMENTE. Não interprete, não complete, não deduza.
- Se não conseguir ler um trecho, omita em vez de adivinhar."""


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
    return {"e_desta_guia": True, "exames": exames, "justificativa": justif}


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
            "eventos_portal": g.get("eventos_portal") or []}


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
- "paciente_lido": nome do paciente escrito no anexo (string; "" se não houver)
- "exames_lidos": lista com TODOS os exames pedidos/citados no anexo, ex.:
  ["panoramica","periapical","interproximal","telerradiografia","documentacao"]
- "data_solicitacao": data escrita no anexo, "DD/MM/AAAA" ou null
- "box_data": [ymin,xmin,ymax,xmax] (valores 0-1000) da data, ou null
- "box_assinatura": [ymin,xmin,ymax,xmax] (0-1000) da assinatura do dentista, ou null

Responda APENAS JSON (sem markdown):
{"anexos": [ {"idx":0, "tipo":"...", "legivel":true, "paciente_lido":"...",
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
        return [float(x) for x in v]
    except Exception:
        return None


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


def _decidir(gem, pg, ctx, pac, pasta_dl, review_dir=None, gto=None,
             eventos_portal=None):
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
                gto_ex |= _ex_pdf            # união: só torna a cobertura MAIS exigente
                if _desta:
                    gto_ex_desta |= _ex_pdf  # os exames REAIS desta guia (p/ mensagem)
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
            data = json.loads(txt)
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
            idx, a, _motivo = _escolher_solicitacao(leituras, pac["nome"], _alvo_ex, len(cands))
            # Falhou SÓ na cobertura de exames? Manuscrito costuma sair sub-lido na
            # 1ª passada (ex.: leu "periapical" e perdeu "panorâmica"). Releitura
            # dirigida do(s) candidato(s) e nova decisão determinística.
            if idx is None and _motivo == "NAO_COBRE":
                _reler_exames_focado(gem, cands, leituras, pac["nome"])
                idx, a, _motivo = _escolher_solicitacao(leituras, pac["nome"], _alvo_ex, len(cands))
            candidato_valido = idx is not None
            if candidato_valido:
                dec = {"indice_solicitacao": idx, "paciente_lido": a.get("paciente_lido"),
                       "exames_lidos": a.get("exames_lidos"),
                       "data_solicitacao": a.get("data_solicitacao"),
                       "box_data": a.get("box_data"), "box_assinatura": a.get("box_assinatura"),
                       "legivel": True, "tipo": a.get("tipo"), "exames_batem": True,
                       "paciente_bate": True, "confianca": "alta", "anexar": True,
                       "leituras": leituras}
            else:
                # transparência p/ a pendência: o que foi LIDO do melhor candidato
                # nome-compatível (a usuária audita "lido" vs "GTO pede")
                _lidos = []
                for _a2 in leituras:
                    if (isinstance(_a2, dict) and _a2.get("tipo") == "solicitacao"
                            and _nomes_compat(_a2.get("paciente_lido") or "", pac["nome"])):
                        _lidos = _a2.get("exames_lidos") or []
                        break
                if _motivo == "NAO_COBRE":
                    _cn = sorted(canon_exames(" ".join(str(e) for e in _lidos)))
                    # MESMO conjunto usado no critério (_alvo_ex). Mensagem e regra
                    # têm de ser a mesma coisa: quando divergiam, a pendência saía
                    # com as duas listas idênticas e parecia um absurdo lógico.
                    # 'documentacao_completa' é token INTERNO (distingue a completa
                    # do controle). Vazava para a mensagem da operadora, que via
                    # "GTO pede ['documentacao', 'documentacao_completa']" — ruído.
                    _pede = sorted(x for x in _alvo_ex if not str(x).startswith("documentacao_"))
                    _falta = sorted(set(_pede) - set(_cn))
                    # Diz O QUE FALTA, não só os dois conjuntos: é o que a operadora
                    # precisa para cobrar o exame certo do dentista.
                    _motivo = (
                        f"NÃO FATUROU porque o pedido do dentista não cobre tudo que a "
                        f"guia autoriza. FALTA no pedido: {lista_amigavel(_falta)}. "
                        f"A guia autoriza {lista_amigavel(_pede)}; o pedido encontrado "
                        f"no prontuário pede {lista_amigavel(_cn)}. "
                        f"O QUE FAZER: pedir à clínica um pedido que inclua "
                        f"{lista_amigavel(_falta)}.")
                elif _motivo == "PACIENTE_INCOMPATIVEL":
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
                dec = {"indice_solicitacao": None, "exames_batem": False,
                       "exames_lidos": _lidos, "paciente_bate": False, "anexar": False,
                       "motivo": _motivo, "leituras": leituras}

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
            
                # Solicitação em PDF: o ajuste de data só sabe editar IMAGEM. Antes o
                # bloco inteiro era pulado em silêncio e o PDF subia com a data VELHA.
                # Só bloqueia quando a data está VENCIDA ('atualizar'); PDF sem data
                # legível ('inserir') segue o fluxo normal, como sempre seguiu.
                if precisa_manipular and tipo == 'atualizar' and "image" not in mime.lower():
                    dec["anexar"] = False
                    dec["motivo"] = ("Solicitação em PDF com data vencida — o ajuste "
                                     "automático só funciona em imagem; revisar")
                    candidato_valido = False
                elif precisa_manipular and "image" in mime.lower():
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
                out["plano_solicitacao"] = cands[idx][0]
                out["solic_idx"] = idx
                if pasta_dl and os.path.isdir(pasta_dl):
                    sname = "SOLICITACAO_" + (re.sub(r"[^A-Za-z0-9._-]+", "_", cands[idx][0]) or "solic")
                    with open(os.path.join(pasta_dl, sname), "wb") as f:
                        f.write(blob)
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
            try:
                r = sess.get(f"{_ODO_API}/v1/gto/imagens"
                             f"?numeroFicha={g['gto']}", timeout=20)
                imgs = r.json() if r.status_code == 200 else []
                nomes = {str(i.get("nomeArquivo", "")) for i in imgs}
                cnt = len(imgs)
            except Exception:
                nomes, cnt = set(), -1
            # Um GTO só é "completo" se JÁ tiver um LAUDO entre os anexos. Antes o
            # código pulava por CONTAGEM (cnt >= 2), então GTO com imagens/solicitação
            # mas SEM laudo (ex.: JOAO PEDRO — 4 anexos, 0 laudo) era marcado como
            # completo e sumia do radar. Agora, sem laudo, ele ENTRA na fila (baixa o
            # laudo real do PRORADIS e anexa; se não houver, vira pendência sem_laudo).
            tem_laudo = any("LAUDO" in str(n).upper() for n in nomes)
            if tem_laudo and (cnt >= 2 or (cnt >= 0 and _ja_anexado_por_nos(nomes))):
                _t(f"[DESC] GTO {g['gto']}: {cnt} anexos (c/ laudo) -> completa, pula "
                   f"| anexos: {sorted(nomes)}")
                with _lock:
                    resultados.append({"gto": g["gto"], "nome": g["nome"],
                                       "status": "JA_ANEXADO",
                                       "anexos_no_portal": sorted(nomes)})
                return
            if cnt >= 2 and not tem_laudo:
                _t(f"[DESC] GTO {g['gto']}: {cnt} anexos mas SEM laudo -> fila (falta laudo)")
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
            except Exception:
                g["eventos_portal"] = []
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
                                   eventos_portal=item.get("eventos_portal"))
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
                    item["anexar_erro"] = (
                        "após excluir exames fora da guia não sobrou nenhum laudo — "
                        "conferir se o exame é do convênio" if excluidos else
                        "nenhum laudo disponível para anexar")
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
                        res = upload_arquivos(gp, arquivos)
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
        raise RuntimeError(
            f"Login no RedeUna/OdontoPrev falhou para o código {_cod} — "
            f"verifique/cadastre a senha do portal. Detalhe: {str(setup['err_odo'])[:140]}")
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
                   if r["status"] in ("SEM_MATCH", "AMBIGUO", "SEM_ARQUIVOS", "JA_ANEXADO", "ERRO")]

    for r in baixados + _outros_res:
        if r.get("status") == "JA_ANEXADO":
            decisoes.append({
                "gto": r["gto"], "paciente": r["nome"], "categoria": "auto",
                "anexado": "OK", "laudo_imgs": [], "solicitacao": None,
                "anexar_solic": False, "justificativa": True, "gto_exames": [],
                "candidatos": [], "solic_idx": None,
                "gemini": {"motivo": "GTO com anexos completos no OdontoPrev"}, "erro": None,
                "arquivos_anexados": r.get("anexos_no_portal") or [],
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
