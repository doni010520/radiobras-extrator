"""doc_completa.py — a DOCUMENTAÇÃO ORTODÔNTICA COMPLETA está mesmo completa?

Dados (17-18/10, 3 meses de demonstrativo RedeUna, 2.315 guias lidas página a
página): a doc completa PAGA leva panorâmica + telerradiografia + traçado + análise
cefalométrica + fotos intra E extrabucais (97-99% das pagas). Faltando qualquer um,
a glosa sobe de 7,6% para 29% ("Envio incompleto, exame(s) não enviado(s): traçado
USP / fotos..."). O robô faturou 15 de 189 assim (fotos ou tele sem folha) — ele
conferia só os LAUDOS de pan e tele, nunca as folhas. Modelo NÃO é exigido (17%).

Duas conferências, só para guia com 'documentacao_completa' (controle/básica fora):
  1. o PDF da tele (CEPH) traz o nome de uma análise -> traçado + análise presentes;
  2. as folhas de entrega (ENTREGA_*) mostram panorâmica, tele, foto intra e foto
     extra — UMA leitura do Gemini para todas as folhas da guia.
Leitura que falha não vira pendência de ninguém: sobe exceção (falha nossa, retry).
"""
from __future__ import annotations

import json
import os
import re

from fitz_seguro import com_fitz

_ANALISE = re.compile(r"RICKETTS|\bUSP\b|STEINER|MC ?NAMARA|TWEED|JARABAK|DOWNS|BJ[OÖ]RK|"
                      r"PADR[AÃ]O\s+PROFIS|\bCENO\b|UNICAMP|AN[AÁ]LISE\s+CEFALOM", re.I)
FOLHAS_EXIGIDAS = ("panoramica", "telerradiografia", "foto_intra", "foto_extra")
_NOME = {"panoramica": "folha da panorâmica", "telerradiografia": "folha da telerradiografia",
         "foto_intra": "fotos intrabucais", "foto_extra": "fotos extrabucais",
         "analise": "traçado/análise cefalométrica no laudo da tele"}


def aplica(exames_canon) -> bool:
    return "documentacao_completa" in set(exames_canon or ())


def _eh_ceph(nome: str) -> bool:
    n = os.path.basename(str(nome)).upper()
    return n.startswith("LAUDO_") and ("CEPH" in n or "TELERRADIOGRAFIA" in n) and n.endswith(".PDF")


@com_fitz
def _texto_pdf(caminho: str) -> str:
    import fitz
    with fitz.open(caminho) as d:
        return " ".join(d.load_page(i).get_text() for i in range(d.page_count))


def tem_analise(arquivos) -> bool | None:
    """True/False se há laudo de tele legível; None se não há laudo de tele (outra
    regra — 'esperando tele' — já cuida disso)."""
    cephs = [a for a in arquivos or [] if _eh_ceph(a) and os.path.isfile(str(a))]
    if not cephs:
        return None
    return any(_ANALISE.search(_texto_pdf(str(a)) or "") for a in cephs)


_PROMPT = """Estas sao as folhas de entrega (com logo RadioBras) de UMA documentacao ortodontica.
Para CADA folha diga o que ela mostra. Responda SO JSON: {"folhas": [{"i": indice, "mostra": [...]}]}
onde "mostra" usa SO estes tokens: "panoramica", "telerradiografia" (cefalometrica lateral/frontal),
"foto_intra" (fotos dentro da boca), "foto_extra" (fotos do rosto/perfil/sorriso),
"periapical", "interproximal", "modelo", "outro". Uma folha pode ter varios."""


def classificar_folhas_gemini(gem, caminhos) -> set:
    """Conjunto do que as folhas mostram. Levanta exceção se não conseguir ler."""
    from google.genai import types
    from esteira import _GEM_MODEL, _gem_cfg, _contar_tokens, preparar_anexo
    parts = []
    for i, p in enumerate(caminhos):
        blob = open(p, "rb").read()
        mime, b2 = preparar_anexo(os.path.basename(p), blob)
        parts += [f"[folha {i}]", types.Part.from_bytes(data=b2 or blob, mime_type=mime or "image/jpeg")]
    parts.append(_PROMPT)
    r = gem.models.generate_content(model=_GEM_MODEL, config=_gem_cfg(), contents=parts)
    _contar_tokens(r)
    t = re.sub(r"^```json|^```|```$", "", (r.text or "").strip(), flags=re.M).strip()
    j = json.loads(t)
    folhas = j.get("folhas") if isinstance(j, dict) else j
    out = set()
    for f in folhas or []:
        out |= {str(x).strip().lower() for x in (f.get("mostra") or [])}
    if not out:
        raise RuntimeError("leitura das folhas da documentação veio vazia")
    return out


def faltas(exames_canon, arquivos, classificar) -> list:
    """Lista do que falta (vazia = completa). `classificar(caminhos) -> set` lê as folhas."""
    if not aplica(exames_canon):
        return []
    falta = []
    if tem_analise(arquivos) is False:
        falta.append("analise")
    folhas = [a for a in arquivos or [] if os.path.basename(str(a)).upper().startswith("ENTREGA_")]
    vistos = classificar(folhas) if folhas else set()
    falta += [f for f in FOLHAS_EXIGIDAS if f not in vistos]
    return falta


def mensagem(falta) -> str:
    itens = ", ".join(_NOME.get(f, f) for f in falta)
    return (f"documentação ortodôntica completa INCOMPLETA: falta {itens}. A operadora paga a "
            f"documentação completa com panorâmica, tele, traçado/análise e fotos intra e "
            f"extrabucais — sem isso glosa por envio incompleto. O QUE FAZER: gerar no PRORADIS "
            f"o que falta; o robô anexa sozinho na próxima rodada.")
