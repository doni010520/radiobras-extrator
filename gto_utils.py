"""
gto_utils.py — Identificação de GTO e leitura determinística do campo
"49 - Observação / Justificativa" (preenchido x vazio). SEM LLM/OCR.

GTO = documento TISS "GUIA TRATAMENTO ODONTOLÓGICO" (PDF com texto).
"""
import re
import unicodedata

import fitz  # PyMuPDF


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


# Marcadores fixos que SÓ aparecem na GTO TISS odontológica.
_GTO_MARKERS = [
    "guia tratamento odontolog",
    "guia de tratamento odontolog",
    "observacao / justificativa",
    "plano de tratamento / procedimentos",
]


def is_gto_text(txt: str) -> bool:
    n = _norm(txt)
    return any(m in n for m in _GTO_MARKERS)


def is_gto_pdf(path: str) -> bool:
    try:
        doc = fitz.open(path)
        txt = "".join(p.get_text() for p in doc)
        doc.close()
        return is_gto_text(txt)
    except Exception:
        return False


# Rótulos de campo (nn - ...) para excluir do conteúdo da observação.
_LABEL_RE = re.compile(r"^\d{1,2}\s*-\s*$|^\d{1,2}-?$")


def _words_display(page):
    """Palavras com bbox no espaço de EXIBIÇÃO (aplica rotação da página)."""
    m = page.rotation_matrix
    out = []
    for x0, y0, x1, y1, w, *_ in page.get_text("words"):
        r = fitz.Rect(x0, y0, x1, y1) * m
        out.append((r.x0, r.y0, r.x1, r.y1, w))
    return out


def extrair_observacao(path: str) -> dict:
    """
    Retorna {is_gto, status: 'PREENCHIDO'|'VAZIO'|'SEM_GTO', conteudo, n_tokens}.
    Estratégia: localizar o rótulo '49 - Observação / Justificativa' e ler os
    tokens dentro da caixa do campo (à direita do rótulo, antes do campo 50),
    descartando rótulos/numeração. Conteúdo não-vazio => PREENCHIDO.
    """
    doc = fitz.open(path)
    page = doc[0]
    full = "".join(p.get_text() for p in doc)
    if not is_gto_text(full):
        doc.close()
        return {"is_gto": False, "status": "SEM_GTO", "conteudo": "", "n_tokens": 0}

    words = _words_display(page)

    def find(sub):
        sub = _norm(sub)
        return [w for w in words if sub in _norm(w[4])]

    # Âncora superior: rótulo "49 - Observação / Justificativa".
    ancora = find("observacao") or find("observa") or find("justificativa")
    if not ancora:
        doc.close()
        return {"is_gto": True, "status": "VAZIO", "conteudo": "", "n_tokens": 0,
                "nota": "rotulo 49 nao localizado"}
    lab_y0 = min(w[1] for w in ancora)
    lab_y1 = max(w[3] for w in ancora)
    lab_x0 = min(w[0] for w in ancora)

    # Âncora inferior: rótulo "50" (assinatura), logo abaixo, à esquerda.
    y50 = None
    for x0, y0, x1, y1, w in words:
        if w.strip() == "50" and y0 > lab_y1 and x0 < lab_x0 + 80:
            y50 = y0 if y50 is None else min(y50, y0)
    if y50 is None:
        y50 = lab_y1 + 18  # fallback: ~uma linha de altura da caixa

    # ROI = caixa do campo 49: abaixo do rótulo 49, acima do rótulo 50, largura toda.
    # (O parágrafo de consentimento fica ACIMA do rótulo 49 e é excluído.)
    roi_y0 = lab_y1 + 1.0
    roi_y1 = y50 - 1.0
    roi_x0 = 8.0
    roi_x1 = page.rect.width - 8.0

    conteudo_tokens = []
    for x0, y0, x1, y1, w in words:
        cy = (y0 + y1) / 2
        if roi_x0 <= (x0 + x1) / 2 <= roi_x1 and roi_y0 <= cy <= roi_y1:
            t = w.strip()
            if not t or _LABEL_RE.match(t):
                continue
            conteudo_tokens.append(t)

    conteudo = re.sub(r"\s+", " ", " ".join(conteudo_tokens)).strip()
    doc.close()
    status = "PREENCHIDO" if conteudo else "VAZIO"
    return {"is_gto": True, "status": status, "conteudo": conteudo,
            "n_tokens": len(conteudo_tokens),
            "roi": [round(roi_x0, 1), round(roi_y0, 1), round(roi_x1, 1), round(roi_y1, 1)]}
