"""
hapvida_pedido.py — Escolhe o PEDIDO DO DENTISTA (encaminhamento, requisito "E")
entre os anexos do prontuário do PRORADIS, para as guias Hapvida que o exigem.

Mesmo princípio do RedeUna: o Gemini só LÊ/transcreve cada anexo; quem escolhe é o
código (esteira._escolher_solicitacao — tipo solicitação, legível, nome compatível,
exames que cobrem, o mais recente vence, folhas do mesmo pedido se somam). A leitura
reaproveita o lote com resgate um-a-um e as releituras dirigidas da esteira.
Nada do RedeUna é alterado; só importado.

Diferenças do Hapvida:
  - só panorâmica, levantamento e documentação exigem pedido (hd.EXIGE_PEDIDO);
    periapical/interproximal da mesma guia não entram na cobertura;
  - a guia do Hapvida não traz o dentista solicitante, então a trava "pedido de
    outro dentista" fica sem dado. Compensação: o pedido tem de ser RECENTE
    (HAPVIDA_PEDIDO_MAX_DIAS, padrão 90 — prazo de execução da guia no manual);
    a data do papel NÃO é reescrita;
  - o canon do RedeUna não conhece "levantamento"/"seriografia". Aqui o texto do
    pedido que diz isso conta como periapical para a cobertura E, se a guia tem
    levantamento, o pedido escolhido tem de dizer levantamento de fato;
  - o portal só aceita GIF/JPG/PNG: pedido em PDF de 1 página vira PNG; PDF de
    várias páginas vira pendência (truncar num upload irreversível, não).
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass, field

import hapvida_decisao as hd
from esteira import (_escolher_solicitacao, _ler_lote_com_resgate, _marcar_origem,
                     _parse_br_date, _data_upload, _pdf_para_imagem, _reler_exames_focado,
                     _reler_nao_classificados, _texto_pedido, _DECISAO_PROMPT, _gem_estado,
                     preparar_anexo)
from fitz_seguro import com_fitz

MAX_CANDIDATOS = 15
# "periapical DE BOCA completa" é o texto impresso mais comum (Lunare, Dr. Thiago — set/26)
_LEVANTAMENTO = re.compile(r"LEVANTAMENT|SERIOGRAF|BOCA\s+TODA|BOCA\s+COMPLET|TODOS\s+OS\s+DENTES|"
                           r"PERIAPICA\w*\s+COMPLET|COMPLET\w*\s+PERIAPICA", re.I)

# alvo de cobertura por código (só os que exigem pedido)
_ALVO = {"81000405": {"panoramica"}, "81000294": {"periapical"}, "81000553": {"documentacao"}}


def max_dias() -> int:
    try:
        return int(os.environ.get("HAPVIDA_PEDIDO_MAX_DIAS", "90"))
    except ValueError:
        return 90


def alvo_pedido(codigos) -> set:
    out = set()
    for c in codigos or []:
        out |= _ALVO.get(str(c), set())
    return out


def _sem_acento(s) -> str:
    return hd._norm(s)


def diz_levantamento(leitura: dict) -> bool:
    return bool(_LEVANTAMENTO.search(_sem_acento(_texto_pedido(leitura))))


def marcar_levantamento(leituras) -> None:
    """Pedido que diz levantamento/seriografia passa a contar como periapical na
    cobertura (o canon compartilhado não conhece o termo)."""
    for a in leituras or []:
        if isinstance(a, dict) and diz_levantamento(a):
            ex = list(a.get("exames_lidos") or [])
            if "periapical" not in ex:
                a["exames_lidos"] = ex + ["periapical"]


@dataclass
class Pedido:
    arquivos: list = field(default_factory=list)   # caminhos prontos p/ o portal
    motivo: str = ""                               # vazio = achou
    responsavel: str = ""
    data: str = ""                                 # data do pedido (lida ou do upload)
    detalhe: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.arquivos) and not self.motivo


_MOTIVOS = {
    "LEITURA_VAZIA": ("Os anexos do prontuário não puderam ser lidos.", "Nós"),
    # casos ADRIANA/ADAILZA (23/09): o papel é da paciente, o nome é que veio
    # rabiscado/abreviado. "nome abreviado" leva à Conferência + botão Confirmei;
    # "documento de outro paciente" mandaria cobrar a clínica à toa.
    "PACIENTE_INCOMPATIVEL": ("Há pedido de exame no prontuário, mas o nome lido no pedido está ABREVIADO "
                              "ou ilegível: o robô não confirma que é deste paciente. Conferir e, se "
                              "for dele, clicar em Confirmei.", "Conferência"),
    "OUTRO_DENTISTA": ("O pedido encontrado foi assinado por OUTRO dentista.", "Conferência"),
}


_GUIA_TXT = re.compile(r"GUIA\s+DE\s+TRATAMENTO\s+ODONTOL|PROFISSIONAL\s+SOLICITANTE|"
                       r"NUMERO\s+GUIA\s+PRINCIPAL|REGISTRO\s+ANS", re.I)


@com_fitz
def eh_guia(mime: str, blob: bytes) -> bool:
    """A GTO do próprio Hapvida arquivada no prontuário NÃO é pedido do dentista:
    ela traz o exame escrito e 'Profissional Solicitante: RADIOBRAS'. Sai da lista
    antes do Gemini (PDF tem texto) — o RedeUna também separa a GTO dos candidatos."""
    if mime != "application/pdf":
        return False
    try:
        import fitz
        with fitz.open(stream=blob, filetype="pdf") as d:
            txt = " ".join(d.load_page(i).get_text() for i in range(min(d.page_count, 2)))
    except Exception:
        return False
    return bool(_GUIA_TXT.search(_sem_acento(txt)))


def leitura_de_guia(a: dict) -> bool:
    """Mesma trava para GTO escaneada (imagem): a transcrição denuncia o formulário."""
    return bool(_GUIA_TXT.search(_sem_acento(str(a.get("texto") or ""))))


def _data_da_leitura(a) -> _dt.date | None:
    return _parse_br_date(a.get("data_solicitacao")) or _data_upload(a.get("arquivo_origem"))


# ── nome abreviado no pedido manuscrito (decisão do dono 17/10) ──────────────
# Histórico set/26 Centro: 22 de 59 pendências eram "nome abreviado/ilegível" em
# pedido escrito à mão ("Jorge Pimentel" p/ JORGE PIMENTEL DOS SANTOS) — a clínica
# anexou e a Hapvida PAGOU. Aceita SÓ com as três provas juntas: 1º nome + 1
# sobrenome batem (tolerando 1-2 letras de grafia), pedido de até 30 dias antes do
# exame e o exame pedido cobrindo a guia. Outro nome (MATHEUS na guia da DEBORA)
# continua recusado.
NOME_PARCIAL_MAX_DIAS = 30
_PARTICULAS = {"DE", "DA", "DO", "DOS", "DAS", "E"}


def _tok_parecido(a: str, b: str) -> bool:
    from difflib import SequenceMatcher
    return a == b or (min(len(a), len(b)) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.8)


def nome_parcial(lido, nome_guia) -> bool:
    """1º nome igual (ou quase) E pelo menos um sobrenome do lido presente no da guia."""
    tl = [t for t in re.sub(r"[^A-Z ]", " ", _sem_acento(lido).upper()).split() if t not in _PARTICULAS]
    tg = [t for t in re.sub(r"[^A-Z ]", " ", _sem_acento(nome_guia).upper()).split() if t not in _PARTICULAS]
    if len(tl) < 2 or len(tg) < 2 or not _tok_parecido(tl[0], tg[0]):
        return False
    return any(_tok_parecido(s, t) for s in tl[1:] for t in tg[1:])


_DATA_PROMPT = (
    "Leia SÓ a data em que este pedido de exame foi emitido/assinado (perto da assinatura, do carimbo "
    "ou em 'Salvador, __ de __ de ____'). Formato brasileiro: DIA primeiro, depois MÊS, depois ANO com "
    "4 dígitos; ano escrito com 2 dígitos ('26') vira 2026. Olhe cada algarismo do ano com atenção "
    "('6' manuscrito parece '0'). Não invente: sem data legível, devolva vazio. "
    'Responda JSON {"data": "DD/MM/AAAA"}')


def _reler_data(gem, cand) -> _dt.date | None:
    """2ª leitura SÓ da data, quando a 1ª deu pedido vencido (FABIO 08/09/2026 lido
    2020; MARIA DA GLORIA '04 de 09' lido 09/04)."""
    import json as _json
    from google.genai import types
    from esteira import _GEM_MODEL, _gem_cfg, _contar_tokens
    try:
        fn, mime, blob, _ = cand
        r = gem.models.generate_content(model=_GEM_MODEL, config=_gem_cfg(),
                                        contents=[types.Part.from_bytes(data=blob, mime_type=mime), _DATA_PROMPT])
        _contar_tokens(r)
        t = re.sub(r"^```json|^```|```$", "", (r.text or "").strip(), flags=re.M).strip()
        return _parse_br_date((_json.loads(t) or {}).get("data"))
    except Exception:
        return None


def _para_portal(fn: str, mime: str, blob: bytes, destino: str, i: int) -> str:
    """Grava o pedido num formato que o portal aceita. PDF de 1 página -> PNG."""
    if mime == "application/pdf":
        r = _pdf_para_imagem(blob)
        if not r:
            raise ValueError("pedido em PDF com mais de uma página: o portal só aceita imagem")
        blob, mime = r
    ext = {"image/png": ".png", "image/gif": ".gif"}.get(mime, ".jpg")
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(fn))[0])[:40] or "pedido"
    p = os.path.join(destino, f"PEDIDO_{i}_{base}{ext}")
    with open(p, "wb") as f:
        f.write(blob)
    return p


def escolher_pedido(gem, arquivos: list, nome: str, codigos, dia: str, destino: str,
                    hoje: _dt.date | None = None, nome_confirmado: bool = False) -> Pedido:
    """arquivos: anexos do prontuário, do MAIS RECENTE para o mais antigo.
    nome_confirmado: SINAL VERDE HUMANO (✔ Confirmei na pendência) — libera a trava
    do nome, como no RedeUna. Tipo, cobertura lida, GTO e data continuam valendo."""
    alvo = alvo_pedido(codigos)
    if not alvo:
        return Pedido()                                   # guia não exige pedido

    # a leitura já caiu nesta execução (crédito/cota/chave): não queima chamada
    if _gem_estado["fatal"]:
        raise RuntimeError("leitura indisponível nesta execução: " + str(_gem_estado["fatal"])[:140])

    cands, descartados = [], []
    for p in (arquivos or [])[:MAX_CANDIDATOS]:
        try:
            blob = open(p, "rb").read()
        except OSError:
            continue
        mime, b2 = preparar_anexo(os.path.basename(p), blob)
        if mime and eh_guia(mime, b2):
            continue
        if mime:
            cands.append((os.path.basename(p), mime, b2, None))
        else:
            descartados.append(os.path.basename(p))
    if not cands:
        if not arquivos:
            return Pedido(motivo="Não há nenhum pedido do dentista: o prontuário do PRORADIS está sem documentos.",
                          responsavel="Clínica")
        return Pedido(motivo=f"Os {len(arquivos)} anexos do prontuário não abriram ({', '.join(descartados[:3])}).",
                      responsavel="Nós")

    from google.genai import types
    contents = []
    for i, (fn, mime, blob, _) in enumerate(cands):
        contents += [f"[anexo {i}]", types.Part.from_bytes(data=blob, mime_type=mime)]
    contents.append(_DECISAO_PROMPT)
    data, _ = _ler_lote_com_resgate(gem, cands, contents)   # erro fatal sobe -> 'erro'
    leituras = (data.get("anexos") if isinstance(data, dict) else data) or []

    def _escolher(nome_ok=False):
        for x in leituras:                        # GTO escaneada nunca é pedido
            if isinstance(x, dict) and x.get("tipo") == "solicitacao" and leitura_de_guia(x):
                x["tipo"] = "outro"
        _marcar_origem(leituras, cands)
        marcar_levantamento(leituras)
        det = {}
        r = _escolher_solicitacao(leituras, nome, alvo, len(cands), "", det, "",
                                  prontuario_confirmado=False,
                                  nome_confirmado=nome_confirmado or nome_ok)
        return r + (det,)

    idx, a, motivo, det = _escolher()
    if idx is None and motivo in ("NAO_COBRE", "PACIENTE_INCOMPATIVEL"):
        if motivo == "NAO_COBRE":
            _reler_exames_focado(gem, cands, leituras, nome)
        _reler_nao_classificados(gem, cands, leituras, nome_gto=nome)
        idx, a, motivo, det = _escolher()

    parcial = False
    if idx is None and motivo == "PACIENTE_INCOMPATIVEL" and any(
            isinstance(x, dict) and x.get("tipo") == "solicitacao"
            and nome_parcial(x.get("paciente_lido") or "", nome) for x in leituras):
        i2, a2, m2, d2 = _escolher(nome_ok=True)
        esc2 = [x for x in leituras if isinstance(x, dict) and x.get("idx") in (d2.get("idxs") or [i2])]
        # o escolhido sem a trava do nome TEM de ser o pedido de nome parcial — nunca o de outra pessoa
        if i2 is not None and esc2 and all(nome_parcial(x.get("paciente_lido") or "", nome) for x in esc2):
            idx, a, motivo, det, parcial = i2, a2, m2, d2, True

    if idx is None:
        if motivo == "NAO_COBRE":
            falta_set = set(det.get("falta") or alvo)
            if "81000294" in {str(c) for c in codigos} and "periapical" in falta_set:
                # o levantamento entra na cobertura como periapical; a pendência fala
                # a língua da guia (SIMONE 09/10 saía "FALTA: periapical")
                falta_set = (falta_set - {"periapical"}) | {"levantamento"}
            falta = ", ".join(sorted(falta_set))
            lidos = ", ".join(det.get("lidos") or []) or "nada legível"
            return Pedido(motivo=(f"O pedido mais recente do dentista não cobre tudo que a guia autoriza: "
                                  f"pede {lidos}. FALTA no pedido: {falta}."), responsavel="Clínica", detalhe=det)
        if motivo == "PACIENTE_INCOMPATIVEL" and not any(
                isinstance(x, dict) and x.get("tipo") == "solicitacao" for x in leituras):
            return Pedido(motivo=(f"Não há nenhum pedido do dentista entre os {len(cands)} documentos do prontuário."),
                          responsavel="Clínica", detalhe=det)
        txt, resp = _MOTIVOS.get(motivo, (f"Pedido não confirmado ({motivo}).", "Conferência"))
        return Pedido(motivo=txt, responsavel=resp, detalhe=det)

    idxs = det.get("idxs") or [idx]
    escolhidas = [x for x in leituras if isinstance(x, dict) and x.get("idx") in idxs]

    d_ped = _data_da_leitura(a)
    d_exame = _parse_br_date(dia) or hoje or _dt.date.today()
    d_up = _data_upload(a.get("arquivo_origem"))
    if d_ped and d_ped > d_exame + _dt.timedelta(days=1):
        # data DEPOIS do exame = mal lida (GLEYDE: "setembro" manuscrito lido como
        # dezembro). Não serve para provar que o pedido é recente: vale o upload.
        d_ped = d_up
    elif (d_ped and d_up and d_ped.year < d_exame.year - 5
            and (d_exame - d_up).days <= max_dias()):
        # ano absurdo com upload recente = leitura errada (PALOMA 09/10: "09/10/26"
        # à mão saiu 26/10/2009). Pedido antigo de verdade (ALICE, 2023/2025) fica
        # dentro dos 5 anos e continua vencido.
        d_ped = d_up
    if d_ped and (d_exame - d_ped).days > max_dias():
        ci = a.get("idx") if isinstance(a.get("idx"), int) else idx
        d2 = _reler_data(gem, cands[ci]) if 0 <= ci < len(cands) else None
        if d2 and d2 <= d_exame + _dt.timedelta(days=1) and (d_exame - d2).days <= max_dias():
            det["data_relida"] = f"{d_ped:%d/%m/%Y} -> {d2:%d/%m/%Y}"
            d_ped = d2
    if parcial:
        d_papel = _parse_br_date(a.get("data_solicitacao"))
        if det.get("data_relida"):
            d_papel = d_ped
        if not (d_papel and d_papel <= d_exame + _dt.timedelta(days=1)
                and (d_exame - d_papel).days <= NOME_PARCIAL_MAX_DIAS):
            txt, resp = _MOTIVOS["PACIENTE_INCOMPATIVEL"]
            return Pedido(motivo=txt, responsavel=resp, detalhe=det)
        det["nome_parcial"] = a.get("paciente_lido")
    if d_ped and (d_exame - d_ped).days > max_dias():
        return Pedido(motivo=(f"Pedido do dentista com data vencida: o mais recente é de {d_ped:%d/%m/%Y}, "
                              f"mais de {max_dias()} dias antes do exame. Falta o pedido atual."),
                      responsavel="Clínica", data=f"{d_ped:%d/%m/%Y}", detalhe=det)

    if "81000294" in {str(c) for c in codigos} and not any(diz_levantamento(x) for x in escolhidas):
        return Pedido(motivo=("A guia é de levantamento radiográfico, mas o pedido do dentista não pede "
                              "levantamento. FALTA no pedido: levantamento."), responsavel="Clínica", detalhe=det)

    os.makedirs(destino, exist_ok=True)
    try:
        saida = [_para_portal(*cands[i][:3], destino, i) for i in idxs]
    except ValueError as e:
        return Pedido(motivo=f"Pedido encontrado, mas {e}.", responsavel="Conferência", detalhe=det)
    return Pedido(arquivos=saida, data=f"{d_ped:%d/%m/%Y}" if d_ped else "", detalhe=det)
