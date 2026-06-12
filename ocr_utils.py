"""
ocr_utils.py — OCR clássico (Tesseract) para solicitações. SEM LLM.

Objetivo: (1) distinguir MANUSCRITA x DIGITADA pela confiança/densidade de
palavras reconhecidas; (2) ler o texto das digitadas para conferências.
"""
import os
import re

import cv2
import numpy as np
import pytesseract

# Binário do Tesseract (Windows). Em Linux/Docker fica no PATH (/usr/bin/tesseract).
_WIN = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if os.path.exists(_WIN):
    pytesseract.pytesseract.tesseract_cmd = _WIN

# tessdata local do projeto (por + eng), evita depender da instalação do SO.
_TESSDATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tessdata")
if os.path.isdir(_TESSDATA):
    os.environ["TESSDATA_PREFIX"] = _TESSDATA
_CFG = "--oem 1 --psm 6 -l por+eng"


def _strip(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


# Dicionário PT (normalizado) para medir validade das palavras do OCR.
_DICT = set()
_DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "palavras_pt.txt")
if os.path.exists(_DICT_PATH):
    with open(_DICT_PATH, encoding="utf-8", errors="ignore") as _f:
        for _w in _f:
            _w = _strip(_w.strip())
            if len(_w) >= 3:
                _DICT.add(_w)

# Léxico de domínio: termos que aparecem em solicitações de exame odontológico.
EXAMES_LEX = {
    "panoramica", "periapical", "telerradiografia", "interproximal", "interprox",
    "documentacao", "cefalometria", "cefalometrica", "radiografia", "tomografia",
    "fotografia", "fotografias", "modelo", "modelos", "analise", "profis",
    "carpal", "mao", "atm", "oclusal", "seios", "tracado", "lateral", "frontal",
}
PEDIDO_LEX = {
    "solicito", "solicitacao", "solicitante", "requisicao", "exame", "exames",
    "paciente", "dente", "regiao", "cro", "dr", "dra", "dentista", "ortodontia",
}


def _preprocess(img):
    """Escala de cinza + binarização Otsu (ajuda o Tesseract em scans)."""
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # upscale se pequeno (melhora OCR)
    h, w = img.shape[:2]
    if max(h, w) < 1500:
        f = 1500 / max(h, w)
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    return img


def ocr_imagem(img) -> dict:
    """
    Roda OCR e retorna métricas determinísticas:
      texto, n_palavras (conf>=60), conf_media (das palavras válidas),
      densidade_dicionario (fração de tokens 'palavra-like' com >=3 letras).
    """
    proc = _preprocess(img)
    data = pytesseract.image_to_data(proc, config=_CFG,
                                     output_type=pytesseract.Output.DICT)
    palavras, confs = [], []
    for txt, conf in zip(data["text"], data["conf"]):
        t = (txt or "").strip()
        try:
            c = float(conf)
        except (TypeError, ValueError):
            c = -1
        if t and c >= 0:
            palavras.append((t, c))
    validas = [(t, c) for t, c in palavras if c >= 60 and len(t) >= 2]
    conf_media = round(float(np.mean([c for _, c in validas])), 1) if validas else 0.0
    # tokens "palavra-like": >=3 letras alfabéticas seguidas
    aceitas = [t for t, c in validas if re.search(r"[A-Za-zÀ-ÿ]{3,}", t)]
    texto = " ".join(t for t, _ in palavras)

    # Validade por DICIONÁRIO PT (sinal real de "texto digitado coerente")
    alfa = [_strip(t) for t in aceitas]
    alfa = [t for t in alfa if len(t) >= 3]
    validas_dic = [t for t in alfa if t in _DICT]
    ratio_dic = round(len(validas_dic) / len(alfa), 3) if alfa else 0.0
    kw_exames = sorted({t for t in alfa if t in EXAMES_LEX})
    kw_pedido = sorted({t for t in alfa if t in PEDIDO_LEX})

    return {
        "texto": re.sub(r"\s+", " ", texto).strip(),
        "n_palavras_validas": len(validas),
        "n_alfa": len(alfa),
        "n_dic": len(validas_dic),
        "ratio_dic": ratio_dic,
        "kw_exames": kw_exames,
        "kw_pedido": kw_pedido,
        "conf_media": conf_media,
    }


def ocr_arquivo(path: str) -> dict:
    arr = np.frombuffer(open(path, "rb").read(), dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"erro": "imdecode falhou", "n_palavras_validas": 0,
                "n_palavras_dic": 0, "conf_media": 0.0, "texto": ""}
    return ocr_imagem(img)
