"""
solicitacao_utils.py — Identificação e análise da SOLICITAÇÃO de exame. SEM LLM.

Pipeline (quando a justificativa da GTO está VAZIA):
  1. classificar cada anexo (GTO / laudo-imagem / NF / ID / solicitação / outro)
  2. identificar a solicitação
  3. manuscrita x digitada  (nº de termos de exame que o OCR acerta)
  4. se digitada: conferir exames (solicitados ⊆ realizados) e dentista (== GTO campo 17)
"""
import os
import re
import unicodedata

import fitz

from ocr_utils import ocr_arquivo, EXAMES_LEX, PEDIDO_LEX, _strip
from gto_utils import is_gto_text
from extrator_arquivos import tem_logo_radiobras

# nº mínimo de termos de exame (OCR) para considerar o CORPO digitado.
MIN_EXAMES_DIGITADA = 2

# Marcadores de outros tipos de anexo (texto normalizado).
_NF_MARK = ["nota fiscal", "nfs-e", "danfe", "prefeitura municipal", "iss qn",
            "nota fiscal de servico", "nfse"]
_ID_MARK = ["habilitacao", "carteira nacional", "registro geral", "documento de identidade",
            "republica federativa do brasil", "ministerio da infraestrutura"]
_PEDIDO_MARK = ["solicito", "solicitacao", "solicitacao de exame", "requisicao",
                "solicitacao de exames"]

# Mapa de exames -> termo canônico (cobre sinônimos da solicitação e da GTO).
_CANON = [
    (r"panor", "panoramica"),
    (r"periap", "periapical"),
    (r"interprox|bite.?wing|bitewing", "interproximal"),
    # 'telerad' (um R só) e 'tele-radio': o dentista escreve à mão e abrevia.
    # Caso MIRLA (22/07 Centro): "Teleradigrafia lateral com tweed e Usp" não casava
    # com `telerr`, a telerradiografia sumia, e a documentação ortodôntica — que
    # exige panorâmica E telerradiografia como âncoras — era reprovada inteira.
    (r"telerr|telerad|tele.?radio|cefalom|ricketts|\bceph\b", "telerradiografia"),
    # "documenta..." + as ABREVIACOES DO PORTAL OdontoPrev, colhidas do catalogo
    # /v1/gto/eventos em 26/07: "Doc Orto Basica/Compl/Espec./Contro", "DocOrtoComp
    # II", "Doc Ortopédica", "Doc Periodontal", "Doc Perio BD", "Doc Diag Imp BD".
    # Sem elas, uma GTO de documentacao vinha com exames VAZIOS -> "GTO ilegivel".
    # Exige "doc" seguido de orto/perio/diag para nao casar "documento"/"doc." solto.
    (r"documenta|\bdoc\.?\s*orto|\bdocorto|\bdoc\.?\s*perio|\bdoc\.?\s*diag", "documentacao"),
    (r"seios?\s*da\s*face", "seios_da_face"),
    # \btc\b com fronteira DOS DOIS LADOS: sem ela, "etc" virava "tomografia" e a
    # solicitacao passava a "cobrir" um exame que ninguem pediu.
    (r"tomograf|\btc\b|cone beam|feixe c", "tomografia"),
    # "Fotos intra e extras bucais" é como o dentista escreve — `fotograf` não pega.
    # \bfotos?\b com fronteira dos dois lados para não casar "fotossensível".
    (r"fotograf|\bfotos?\b", "fotografia"),
    (r"modelo", "modelo"),
    (r"carpal|\bmao\b|idade ossea", "carpal"),
    (r"\batm\b", "atm"),
    (r"oclus", "oclusal"),
]


# ── Tolerância a erro de grafia ───────────────────────────────────────────────
# Acrescentar um padrão ao _CANON a cada erro descoberto é corrida perdida: o
# pedido é manuscrito e cada dentista escreve de um jeito. Aqui a comparação é por
# DISTÂNCIA DE EDIÇÃO contra um vocabulário FECHADO — o mesmo mecanismo que o
# código já usa para nome de paciente ("IONICE"/"JONICE").
#
# Guardas, porque reconhecer exame que não foi pedido faz uma solicitação "cobrir"
# o que ela não cobre — e aí o sistema fatura errado:
#   - só palavras LONGAS (>=8 letras): palavra curta colide fácil;
#   - tolerância de UMA letra, sempre. Com 2, 'tomografia' e 'fotografia' passam a
#     colidir — exame caro confundido com foto. Uma letra já resolve o caso real
#     ('teleradigrafia' está a 1 de 'teleradiografia');
#   - EMPATE entre exames diferentes = descarta. Na dúvida, não reconhece;
#   - vocabulário fechado, nunca casamento livre.
_VOCAB = {
    "panoramica": "panoramica", "panoramicas": "panoramica",
    "periapical": "periapical", "periapicais": "periapical",
    "interproximal": "interproximal", "interproximais": "interproximal",
    "telerradiografia": "telerradiografia", "teleradiografia": "telerradiografia",
    "telerradiografias": "telerradiografia", "cefalometrica": "telerradiografia",
    "documentacao": "documentacao", "documentacoes": "documentacao",
    "tomografia": "tomografia", "tomografias": "tomografia",
    "fotografia": "fotografia", "fotografias": "fotografia",
    "radiografia": None,      # genérico demais: existe no vocabulário só para
                              # ABSORVER a palavra e impedir que ela seja casada
                              # por engano com 'telerradiografia' (distância 5).
}


def dist_edicao(a: str, b: str, teto: int = 2) -> int:
    """Levenshtein com corte em `teto` — só interessa saber se é erro de grafia."""
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


_FUZZY_TETO = 1


def _fuzzy_exames(n: str) -> set:
    """Exames reconhecidos por aproximação de grafia. `n` já normalizado."""
    out = set()
    for tok in re.findall(r"[a-z]{8,}", n):
        cand = {}                      # canon -> menor distância encontrada
        for palavra, canon in _VOCAB.items():
            d = dist_edicao(tok, palavra, _FUZZY_TETO)
            if d <= _FUZZY_TETO and d < cand.get(canon, _FUZZY_TETO + 1):
                cand[canon] = d
        if not cand:
            continue
        melhor_d = min(cand.values())
        vencedores = [c for c, d in cand.items() if d == melhor_d]
        # empate entre exames DIFERENTES -> não dá pra saber qual é; descarta.
        # (None no vocabulário é palavra-isca, tipo 'radiografia': absorve o token
        #  e impede que ele seja atribuído a um exame por aproximação.)
        if len(vencedores) == 1 and vencedores[0] is not None:
            out.add(vencedores[0])
    return out


def canon_exames(texto: str) -> set:
    n = _strip(texto)
    out = set()
    for pat, canon in _CANON:
        if re.search(pat, n):
            out.add(canon)
    out |= _fuzzy_exames(n)
    return out


# ── Documentação x seus componentes ───────────────────────────────────────────
# A GTO pede o procedimento fechado ("Doc Orto Compl" -> 'documentacao'). A
# solicitação do dentista costuma escrever os COMPONENTES ("panorâmica, telerra-
# diografia, fotografias, modelos, periapicais"). O issubset falhava — {documentacao}
# não está contido em {panoramica, telerradiografia, ...} — e uma solicitação
# CORRETA, e mais detalhada que a exigência, virava pendência.
#
# Âncoras, não só contagem: uma documentação ortodôntica sempre tem panorâmica E
# telerradiografia. Só contar componentes aceitaria {periapical, oclusal,
# fotografia}, que não é documentação.
#
# LIMITAÇÃO CONHECIDA: o _CANON colapsa "Doc Orto", "Doc Periodontal" e "Doc Diag
# Imp" no mesmo token 'documentacao', e a periodontal é de periapicais completos —
# não tem telerradiografia, então NÃO passa por aqui e continua virando pendência
# (comportamento de hoje, não regride). A correção estrutural é canonizar o
# subtipo; esta função é o passo conservador.
_DOC_ANCORAS = {"panoramica", "telerradiografia"}
_DOC_COMPONENTES = {"panoramica", "telerradiografia", "fotografia",
                    "modelo", "periapical", "oclusal", "carpal"}


def componentes_da_documentacao(ex: set) -> set:
    """Direção OPOSTA da expande_documentacao(), para o lado da GUIA.

    Uma guia que autoriza 'documentacao' é cumprida pelos laudos dos COMPONENTES
    (LAUDO_PANORAMICA, LAUDO_TELERRADIOGRAFIA...). Sem isso, o filtro de exame
    particular lia 'panoramica' no nome do arquivo, não achava 'panoramica' na
    guia (que diz 'documentacao') e descartava o laudo como se fosse exame
    particular — anexando a guia SEM LAUDO, ou com zero arquivo, e ainda assim
    registrando como faturada. Casos DARLAN/JOEL/ROSEANGELA, 20/07 Centro."""
    ex = set(ex or ())
    return (ex | _DOC_COMPONENTES) if "documentacao" in ex else ex


def expande_documentacao(ex: set) -> set:
    """Solicitação que pede os COMPONENTES cobre uma GTO de documentação.

    Aplicar SÓ ao conjunto LIDO DA SOLICITAÇÃO — a direção importa. Pedir os
    componentes satisfaz uma guia de documentação; pedir 'documentação' NÃO
    satisfaz uma guia que exige panorâmica avulsa."""
    ex = set(ex or ())
    if "documentacao" in ex:
        return ex
    if _DOC_ANCORAS <= ex and len(ex & _DOC_COMPONENTES) >= 3:
        return ex | {"documentacao"}
    return ex


def _tem(texto: str, marks: list) -> bool:
    n = _strip(texto)
    return any(m in n for m in marks)


# ── GTO: campo 17 (dentista solicitante) e exames autorizados ─────────────────

def _words_display(page):
    m = page.rotation_matrix
    out = []
    for x0, y0, x1, y1, w, *_ in page.get_text("words"):
        r = fitz.Rect(x0, y0, x1, y1) * m
        out.append((r.x0, r.y0, r.x1, r.y1, w))
    return out


def gto_solicitante(gto_path: str) -> str:
    """Nome do '17 - Nome do Profissional Solicitante' (caixa abaixo do rótulo 17,
    à esquerda, antes do campo 21/22)."""
    doc = fitz.open(gto_path)
    pg = doc[0]
    words = _words_display(pg)
    lab17 = [w for w in words if w[4].strip() == "17" and w[0] < 40]
    lab21 = [w for w in words if w[4].strip() == "21" and w[0] < 40]
    if not lab17:
        doc.close()
        return ""
    y_top = lab17[0][3]
    y_bot = (min(w[1] for w in lab21) if lab21 else y_top + 16)
    # limite à direita: coluna do campo 22 (executante) ~ x>=190
    nome = [w[4] for w in words
            if y_top < (w[1] + w[3]) / 2 < y_bot and 8 < w[0] < 190
            and re.search(r"[A-Za-zÀ-ÿ]{2,}", w[4]) and w[4].strip() not in ("21", "22")]
    doc.close()
    return re.sub(r"\s+", " ", " ".join(nome)).strip()


def gto_exames(gto_path: str) -> set:
    """Exames autorizados/realizados na GTO (a partir das descrições de procedimento)."""
    doc = fitz.open(gto_path)
    txt = "".join(p.get_text() for p in doc)
    doc.close()
    return canon_exames(txt)


# Qualquer sinal de exame RADIOLÓGICO no texto da GTO (mesmo que o exame especifico
# nao esteja no _CANON). Na GTO os radiologicos vem como "Rx <nome>" / "Radiografia
# <nome>". Serve de guarda-corpo: se ha radiografia, EXIGE laudo — nao depende do
# _CANON cobrir 100% dos exames.
_RADIOLOGICO_RE = re.compile(
    r"\brx\b|radiograf|raio.?-?x|panor|periap|interprox|bite.?wing|telerr|cefalom|"
    r"ricketts|\bceph\b|tomograf|cone beam|\btc\b|\batm\b|carpal|idade ossea|"
    r"oclus|seios? da face|sialograf|mastoid|perfil|hand.?wrist")


def gto_dispensa_laudo(gto_path: str) -> bool:
    """True SÓ se a GTO é EXCLUSIVAMENTE modelo de gesso / fotografia — ou seja, tem
    esses termos E NENHUM indicador de exame radiológico. CONSERVADOR: qualquer sinal
    de radiografia (Rx/Radiografia/etc.) -> False (exige laudo), mesmo que o exame
    nao esteja no _CANON. Na duvida, exige laudo."""
    try:
        doc = fitz.open(gto_path)
        txt = "".join(p.get_text() for p in doc)
        doc.close()
    except Exception:
        return False
    n = _strip(txt)
    tem_modelo_foto = bool(re.search(r"\bmodelo|\bfotograf", n))
    tem_radiologico = bool(_RADIOLOGICO_RE.search(n))
    return tem_modelo_foto and not tem_radiologico


# ── Classificação de anexos ───────────────────────────────────────────────────

def tipo_anexo(path: str) -> dict:
    """Classifica um anexo. Para imagens não-laudo, roda OCR (necessário)."""
    ext = os.path.splitext(path)[1].lower()
    body = open(path, "rb").read()
    info = {"arquivo": os.path.basename(path), "tipo": "OUTRO"}

    if body[:4] == b"%PDF":
        doc = fitz.open(stream=body, filetype="pdf")
        txt = "".join(p.get_text() for p in doc)
        doc.close()
        if is_gto_text(txt):
            info["tipo"] = "GTO"
        elif _tem(txt, _NF_MARK):
            info["tipo"] = "NF"
        elif _tem(txt, _PEDIDO_MARK):
            info["tipo"] = "SOLICITACAO"
            info["fonte"] = "pdf_texto"
            info["texto"] = txt
            info["kw_exames"] = sorted(canon_exames(txt))
        else:
            info["tipo"] = "PDF_OUTRO"
        return info

    # imagem — OCR SEMPRE (sinais de solicitação têm prioridade sobre o logo verde,
    # pois clínicas como TOP DENT também usam logo verde -> evita falso "laudo").
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"):
        r = ocr_arquivo(path)
        kw_exames = canon_exames(r["texto"])
        info["ocr"] = {k: r[k] for k in ("n_dic", "ratio_dic", "conf_media")}
        info["kw_exames"] = sorted(kw_exames)
        info["kw_pedido"] = r["kw_pedido"]
        info["texto"] = r["texto"]
        # Solicitação: marcador de pedido OU >=2 termos de exame (corpo de pedido).
        eh_solic = (_tem(r["texto"], _PEDIDO_MARK) or bool(r["kw_pedido"])
                    or len(kw_exames) >= 2)
        if _tem(r["texto"], _ID_MARK):
            info["tipo"] = "ID"
        elif eh_solic:
            info["tipo"] = "SOLICITACAO"
            info["fonte"] = "ocr"
        else:
            # sem sinais de pedido: aí sim o logo verde indica imagem de laudo
            try:
                logo = tem_logo_radiobras(body)
            except Exception:
                logo = False
            info["tipo"] = "LAUDO_IMG" if logo else "IMG_OUTRO"
    return info


def _score_solic(a: dict) -> int:
    return len(a.get("kw_exames", [])) * 2 + len(a.get("kw_pedido", []))


def analisar_paciente(pasta: str, gto_path: str, solicitante_gto: str,
                      exames_realizados: set, gto_text: str = "") -> dict:
    """Identifica a solicitação na pasta e faz as conferências."""
    anexos = []
    for fn in sorted(os.listdir(pasta)):
        fp = os.path.join(pasta, fn)
        if os.path.isfile(fp):
            try:
                anexos.append(tipo_anexo(fp))
            except Exception as e:
                anexos.append({"arquivo": fn, "tipo": "ERRO", "erro": str(e)})

    solics = [a for a in anexos if a["tipo"] == "SOLICITACAO"]
    res = {"anexos": [{"arquivo": a["arquivo"], "tipo": a["tipo"]} for a in anexos]}
    if not solics:
        res["status"] = "SEM_SOLICITACAO"
        res["detalhe"] = "nenhum anexo identificado como solicitação"
        return res

    solic = max(solics, key=_score_solic)
    res["solicitacao"] = solic["arquivo"]
    n_ex = len(solic.get("kw_exames", []))

    # PDF com texto = sempre digitada. Imagem: regra do nº de termos de exame.
    digitada = solic.get("fonte") == "pdf_texto" or n_ex >= MIN_EXAMES_DIGITADA
    if not digitada:
        res["status"] = "MANUSCRITA_REVISAO_HUMANA"
        res["detalhe"] = f"solicitação manuscrita ({n_ex} termo(s) de exame legíveis)"
        return res

    # Digitada -> conferências
    res["status"] = "DIGITADA"
    solic_exames = set(solic.get("kw_exames", []))
    res["exames_solicitados"] = sorted(solic_exames)
    res["exames_realizados"] = sorted(exames_realizados)
    if not exames_realizados:
        # não foi possível ler os exames da GTO -> não afirmar (evita falso negativo)
        res["exames_conferem"] = "INDETERMINADO"
        res["exames_nao_realizados"] = []
    else:
        faltando = solic_exames - exames_realizados
        res["exames_nao_realizados"] = sorted(faltando)
        res["exames_conferem"] = not faltando

    # Dentista: confere por (1) número do CRO da solicitação presente na GTO, ou
    # (2) >=2 tokens do nome do solicitante. O CRO é o sinal mais robusto (números
    # o OCR lê bem e são únicos; o nome pode vir cortado no OCR).
    txt_solic = _strip(solic.get("texto", ""))
    toks = [t for t in re.findall(r"[a-z]{4,}", _strip(solicitante_gto))]
    hits = [t for t in toks if t in txt_solic]
    m = re.search(r"cro[\s:.\-/ba]*?(\d{3,6})", txt_solic)
    cro_solic = m.group(1) if m else None
    gto_digits = re.sub(r"\D", " ", gto_text)
    cro_match = bool(cro_solic and re.search(rf"\b{cro_solic}\b", gto_digits))
    res["dentista_gto"] = solicitante_gto
    res["cro_solicitacao"] = cro_solic
    res["dentista_confere"] = cro_match or len(hits) >= 2
    res["dentista_motivo"] = ("cro" if cro_match else ("nome" if len(hits) >= 2 else "nao_confere"))
    res["dentista_tokens_batem"] = hits
    return res
