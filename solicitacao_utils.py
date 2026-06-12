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
    (r"telerr|cefalom|ricketts|\bceph\b|tele radio", "telerradiografia"),
    (r"documenta", "documentacao"),
    (r"tomograf|tc\b|cone beam|feixe c", "tomografia"),
    (r"fotograf", "fotografia"),
    (r"modelo", "modelo"),
    (r"carpal|\bmao\b|idade ossea", "carpal"),
    (r"\batm\b", "atm"),
    (r"oclus", "oclusal"),
]


def canon_exames(texto: str) -> set:
    n = _strip(texto)
    out = set()
    for pat, canon in _CANON:
        if re.search(pat, n):
            out.add(canon)
    return out


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
