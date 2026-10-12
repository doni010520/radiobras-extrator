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
from fitz_seguro import com_fitz

# Início do CORPO do laudo (a partir daqui vêm achados/recomendações -> ignorar,
# senão "recomendamos radiografia periapical" contaria como exame coberto).
_BODY_RE = re.compile(r"análise:|analise:|impress[aã]o\s+diagn|denti[cç][aã]o|dente\(s\)|^\s*-")


@com_fitz
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

# Fim do CABECALHO num texto corrido (sem quebras de linha): a partir daqui vem o
# corpo, onde "recomendamos radiografia periapical" NAO e exame coberto.
_FIM_CAB = re.compile(r"an[aá]lise\s*:|achados|impress[aã]o\s+diagn|denti[cç][aã]o\s|"
                      r"dente\(s\)|dentes\s+ausentes|interpreta[cç][aã]o|aus[eê]ncia|"
                      r"girovers|presen[cç]a\s+de|recomend|sugere|observ|no\s+exame|"
                      r"elemento\(s\)|tratamento\s+endod|desgaste|m[eé]sio", re.I)
_CAB_EXPL = re.compile(r"(radiografias?\s+analisad\w*|exames?\s+realizad\w*|"
                       r"foi\s+solicitad\w*|foram\s+solicitad\w*)\s*:?", re.I)


def exames_cobertos_texto(texto: str) -> set:
    """Mesma ideia de exames_cobertos, sobre o TEXTO do laudo (aceita texto corrido).
    Junta o titulo (logo apos 'Solicitante: <nome>') e as declaracoes explicitas
    ('Radiografias Analisadas:', 'Exame realizado:', 'Foi solicitada'), cortando no
    inicio do corpo. Validado em 11/10 contra 1057 laudos de panoramica (100 dias)."""
    t = re.sub(r"\s+", " ", texto or "")
    partes = []
    m = re.search(r"solicitante\s*:", t, re.I)
    if m:
        resto = t[m.end():m.end() + 400]
        f = _FIM_CAB.search(resto)
        partes.append(resto[:f.start()] if f else resto[:200])
    for m in _CAB_EXPL.finditer(t[:1500]):
        resto = t[m.end():m.end() + 300]
        f = _FIM_CAB.search(resto)
        partes.append(resto[:f.start()] if f else resto[:200])
    cab = " ".join(partes)
    out = canon_exames(cab)
    # "Levantamento radiografico" e o nome do PRORADIS para periapical de boca toda
    if re.search(r"levantamento", cab, re.I):
        out.add("periapical")
    return out


@com_fitz
def exames_cobertos_pdf(laudo_pdf_path: str) -> set:
    """Exames que o laudo DECLARA cobrir (cabecalho/titulo), lendo o PDF. So a leitura
    por texto (validada em 1057 laudos): a exames_cobertos antiga (por linhas) nao
    corta em "Ausencia..." e contava "recomendamos radiografia periapical"."""
    try:
        d = fitz.open(laudo_pdf_path)
        t = " ".join(pg.get_text() for pg in d)
        d.close()
    except Exception:
        return set()
    return exames_cobertos_texto(t)

