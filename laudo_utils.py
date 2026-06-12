"""
laudo_utils.py — Detecção determinística de LAUDO COMBINADO. SEM LLM.

Padrão recorrente: o laudo da PANORÂMICA já inclui o laudo de exames associados
(periapical, interproximal) que ficam "A Laudar" na worklist. O próprio laudo
declara isso nas PRIMEIRAS LINHAS, em uma de duas formas:
  - título descritivo: "Panorâmica da face e periapical boca completa"
  - lista explícita:    "Radiografias Analisadas: Radiografia Panorâmica, interproximais"
Basta ler o cabeçalho e procurar o termo do exame.
"""
import re

import fitz

from solicitacao_utils import canon_exames, _strip

# Início do CORPO do laudo (a partir daqui vêm achados/recomendações -> ignorar,
# senão "recomendamos radiografia periapical" contaria como exame coberto).
_BODY_RE = re.compile(r"análise:|analise:|impress[aã]o\s+diagn|denti[cç][aã]o|dente\(s\)|^\s*-")


def exames_cobertos(laudo_pdf_path: str) -> set:
    """Exames canônicos DECLARADOS no cabeçalho do laudo (título + linha
    'Radiografias Analisadas:'), antes do corpo. Evita recomendações."""
    doc = fitz.open(laudo_pdf_path)
    txt = doc[0].get_text() if doc.page_count else ""
    doc.close()
    linhas = [l.strip() for l in txt.splitlines() if l.strip()]

    # Começar após "Solicitante:"; coletar linhas de declaração até o corpo.
    ini = 0
    for i, l in enumerate(linhas):
        if "solicitante" in _strip(l):
            ini = i + 1
            break
    decl = []
    for l in linhas[ini:ini + 6]:
        if _BODY_RE.search(_strip(l)) or re.match(r"^\s*-", l):
            break
        decl.append(l)
    # Sempre incluir uma linha explícita "Radiografias Analisadas: ..." se houver.
    for l in linhas[:14]:
        if "radiografias analisadas" in _strip(l):
            decl.append(l)
            break
    return canon_exames(" ".join(decl))


def laudo_combina(laudo_pdf_path: str, exame_pendente_canon: str) -> dict:
    """Confirma se `exame_pendente_canon` (ex.: 'periapical') está incluído no laudo.
    Retorna {cobertos, incluido, trecho}."""
    cobertos = exames_cobertos(laudo_pdf_path)
    return {
        "cobertos": sorted(cobertos),
        "incluido": exame_pendente_canon in cobertos,
    }


_STATUS_PENDENTE = ("A LAUDAR", "LAUDANDO")


def exames_pendentes_reais(laudo_pdf_paths: list, exames_status: list) -> dict:
    """Separa exames 'A Laudar'/'Laudando' em PENDENTES REAIS vs INCLUÍDOS no laudo
    combinado (panorâmica). `exames_status`: lista de (exame_texto, status).
    Retorna {pendentes_reais:[(ex,st)], incluidos:[(ex,st)], cobertos:[...]}"""
    cobertos = set()
    for p in laudo_pdf_paths or []:
        try:
            cobertos |= exames_cobertos(p)
        except Exception:
            pass
    pend, incl = [], []
    for ex, st in exames_status:
        if (st or "").upper().strip() not in _STATUS_PENDENTE:
            continue
        exc = canon_exames(ex or "")
        (incl if (exc and exc & cobertos) else pend).append((ex, st))
    return {"pendentes_reais": pend, "incluidos": incl, "cobertos": sorted(cobertos)}
