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

MAX_CANDIDATOS = 15
_LEVANTAMENTO = re.compile(r"LEVANTAMENT|SERIOGRAF|BOCA\s+TODA|TODOS\s+OS\s+DENTES|"
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
    "PACIENTE_INCOMPATIVEL": ("Há pedido de exame no prontuário, mas nenhum documento do prontuário está "
                              "no nome deste paciente (legível). Conferir se o pedido é dele.", "Conferência"),
    "OUTRO_DENTISTA": ("O pedido encontrado foi assinado por OUTRO dentista.", "Conferência"),
}


def _data_da_leitura(a) -> _dt.date | None:
    return _parse_br_date(a.get("data_solicitacao")) or _data_upload(a.get("arquivo_origem"))


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
                    hoje: _dt.date | None = None) -> Pedido:
    """arquivos: anexos do prontuário, do MAIS RECENTE para o mais antigo."""
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

    def _escolher():
        _marcar_origem(leituras, cands)
        marcar_levantamento(leituras)
        det = {}
        r = _escolher_solicitacao(leituras, nome, alvo, len(cands), "", det, "",
                                  prontuario_confirmado=False, nome_confirmado=False)
        return r + (det,)

    idx, a, motivo, det = _escolher()
    if idx is None and motivo in ("NAO_COBRE", "PACIENTE_INCOMPATIVEL"):
        if motivo == "NAO_COBRE":
            _reler_exames_focado(gem, cands, leituras, nome)
        _reler_nao_classificados(gem, cands, leituras, nome_gto=nome)
        idx, a, motivo, det = _escolher()

    if idx is None:
        if motivo == "NAO_COBRE":
            falta = ", ".join(sorted(det.get("falta") or alvo))
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

    if "81000294" in {str(c) for c in codigos} and not any(diz_levantamento(x) for x in escolhidas):
        return Pedido(motivo=("A guia é de levantamento radiográfico, mas o pedido do dentista pede só "
                              "periapical. FALTA no pedido: levantamento."), responsavel="Clínica", detalhe=det)

    d_ped = _data_da_leitura(a)
    d_exame = _parse_br_date(dia) or hoje or _dt.date.today()
    if d_ped and (d_exame - d_ped).days > max_dias():
        return Pedido(motivo=(f"Pedido do dentista com data vencida: o mais recente é de {d_ped:%d/%m/%Y}, "
                              f"mais de {max_dias()} dias antes do exame. Falta o pedido atual."),
                      responsavel="Clínica", data=f"{d_ped:%d/%m/%Y}", detalhe=det)

    os.makedirs(destino, exist_ok=True)
    try:
        saida = [_para_portal(*cands[i][:3], destino, i) for i in idxs]
    except ValueError as e:
        return Pedido(motivo=f"Pedido encontrado, mas {e}.", responsavel="Conferência", detalhe=det)
    return Pedido(arquivos=saida, data=f"{d_ped:%d/%m/%Y}" if d_ped else "", detalhe=det)
