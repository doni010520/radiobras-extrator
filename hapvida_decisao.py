"""
hapvida_decisao.py — Regras de faturamento Hapvida +Odonto. Lógica PURA (sem rede).

Escopo (dono, 13/09/2026): o robô SÓ FATURA — anexa o que falta em guias que já
nasceram executadas no login da RADIOBRAS. Nunca cria nem executa guia.

O que anexar (Tabela de Procedimentos + Guia de Rotina de Atendimento):
  - SEMPRE o entregável RadioBras (ENTREGA_*): imagem "devidamente identificada".
  - Pedido do dentista (encaminhamento) quando o exame exige "E".
  - Laudo NÃO é requisito desses exames.
  - Só GIF/JPG/PNG, até 2 MB; anexar no dia do atendimento.

Princípios herdados do RedeUna (DECISOES.md): o código decide de forma
determinística; nunca anexa sem identidade batendo; o que é externo vira pendência.
"""
from __future__ import annotations

import io
import os
import re
import unicodedata
from dataclasses import dataclass, field

CODIGOS = {
    "81000405": "panorâmica",
    "81000375": "interproximal",
    "81000421": "periapical",
    "81000294": "levantamento radiográfico",
    "81000553": "documentação ortodôntica",
}
# Requisito "E" (encaminhamento do dentista assistente) na Tabela de Procedimentos.
EXIGE_PEDIDO = {"81000405", "81000294", "81000553"}

EXTENSOES_OK = {".jpg", ".jpeg", ".png", ".gif"}
LIMITE_BYTES = 2 * 1024 * 1024

# categorias gravadas em execucao_itens.categoria (String(20))
JA_ANEXADA = "ja_anexada"
AUTO = "auto"
SEM_ENTREGAVEL = "sem_entregavel"
SEM_PEDIDO = "sem_solicitacao"
NAO_ACHADO = "nao_achado"
AMBIGUO = "revisao"
IDENTIDADE = "identidade"
CODIGO_DESCONHECIDO = "revisao"


@dataclass
class Decisao:
    categoria: str
    anexar: list = field(default_factory=list)   # caminhos, na ordem de envio
    motivo: str = ""
    responsavel: str = ""                        # Clínica | Radiologista | Cadastro | Conferência | Nós

    @property
    def faturavel(self) -> bool:
        return self.categoria == AUTO and bool(self.anexar)


# ── Lista do dia ─────────────────────────────────────────────────────────────
def agrupar_por_guia(linhas: list[dict]) -> dict[str, dict]:
    """Linhas de Procedimentos Executados -> {guia: {usuario, nome, dia, itens}}.
    Mantém a ordem dos itens (é a ordem dos checkboxes do formulário de upload)."""
    guias: dict[str, dict] = {}
    for ln in linhas:
        g = guias.setdefault(str(ln["guia"]), {
            "guia": str(ln["guia"]), "usuario": ln["usuario"], "nome": ln["nome"],
            "dia": (ln.get("dt_atendimento") or "")[:10], "itens": []})
        g["itens"].append({"codigo": ln["codigo"], "procedimento": ln.get("procedimento", ""),
                           "dente": ln.get("dente", "")})
    return guias


# ── Identidade ───────────────────────────────────────────────────────────────
def _norm(s) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^A-Za-z ]", " ", s).upper().split())


def _nasc(s) -> str:
    d = re.sub(r"\D", "", str(s or ""))
    if len(d) == 8 and d[:4].startswith(("19", "20")):       # AAAAMMDD
        return d[6:8] + d[4:6] + d[:4]
    return d if len(d) == 8 else ""                           # DDMMAAAA


def nomes_compativeis(a, b) -> bool:
    """Mesmo primeiro nome e todos os tokens do nome mais curto no mais longo.
    Cobre o nome truncado da lista do Hapvida ('JORGE PIMENTEL') contra o completo."""
    ta, tb = _norm(a).split(), _norm(b).split()
    if not ta or not tb or ta[0] != tb[0]:
        return False
    curto, longo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return all(t in longo for t in curto)


def identidade_confere(paciente: dict, proradis: dict) -> tuple[bool, str]:
    """Trava de identidade. Nome tem de ser compatível; nascimento, se os dois
    lados tiverem, tem de ser IGUAL. Nascimento divergente barra mesmo com nome igual."""
    if not nomes_compativeis(paciente.get("nome"), proradis.get("nome")):
        return False, (f"nome no Hapvida ({paciente.get('nome')}) não bate com o do "
                       f"PRORADIS ({proradis.get('nome')})")
    na, nb = _nasc(paciente.get("nascimento")), _nasc(proradis.get("nascimento"))
    if na and nb and na != nb:
        return False, (f"nascimento diverge: Hapvida {paciente.get('nascimento')} × "
                       f"PRORADIS {proradis.get('nascimento')}")
    return True, ""


# ── Decisão por guia ─────────────────────────────────────────────────────────
def decidir_guia(guia: dict, ja_anexada: bool, paciente: dict | None,
                 proradis: dict | None) -> Decisao:
    """proradis: {encontrado, ambiguo, nome, nascimento, entregaveis:[path],
    pedido:[path]|path|None, pedido_motivo, pedido_responsavel}."""
    if ja_anexada:
        return Decisao(JA_ANEXADA, motivo="Guia já tem imagem no Banco de Imagens (anexada antes).")

    desconhecidos = sorted({i["codigo"] for i in guia["itens"]} - set(CODIGOS))
    if desconhecidos:
        return Decisao(CODIGO_DESCONHECIDO, responsavel="Conferência",
                       motivo=f"Exame fora da regra do robô ({', '.join(desconhecidos)}): conferir manualmente.")

    pac = paciente or {"nome": guia.get("nome"), "nascimento": ""}
    if not proradis or not proradis.get("encontrado"):
        return Decisao(NAO_ACHADO, responsavel="Cadastro",
                       motivo=f"Exame de {pac.get('nome')} do dia {guia.get('dia')} não foi encontrado no PRORADIS.")
    if proradis.get("ambiguo"):
        return Decisao(AMBIGUO, responsavel="Conferência",
                       motivo="Mais de um paciente compatível no PRORADIS nesse dia — o robô não escolhe.")

    ok, porque = identidade_confere(pac, proradis)
    if not ok:
        return Decisao(IDENTIDADE, responsavel="Conferência",
                       motivo=f"Identidade não confirmada: {porque}. Nada foi anexado.")

    if proradis.get("misto"):
        fora = ", ".join(proradis.get("exames_fora") or []) or "exame de fora"
        return Decisao(AMBIGUO, responsavel="Conferência",
                       motivo=(f"No mesmo atendimento há exame particular ou de outro convênio ({fora}). "
                               "As folhas de imagem não dizem de qual exame são; o robô não separa. Nada foi anexado."))

    entregaveis = list(proradis.get("entregaveis") or [])
    if not entregaveis:
        return Decisao(SEM_ENTREGAVEL, responsavel="Radiologista",
                       motivo="Ainda não tem entregável RadioBras (folha com a imagem) no PRORADIS.")

    # Norma Hapvida não pede laudo. HAPVIDA_EXIGE_LAUDO=1 liga a segurança do RedeUna
    # (glosa PALOMA): exige que o laudo EXISTA no PRORADIS, sem anexá-lo.
    if os.environ.get("HAPVIDA_EXIGE_LAUDO") == "1" and not proradis.get("laudos"):
        return Decisao(SEM_ENTREGAVEL, responsavel="Radiologista",
                       motivo="Imagem pronta, mas falta o LAUDO no PRORADIS.")

    precisa_pedido = any(i["codigo"] in EXIGE_PEDIDO for i in guia["itens"])
    pedido = proradis.get("pedido")
    pedidos = list(pedido) if isinstance(pedido, (list, tuple)) else ([pedido] if pedido else [])
    if precisa_pedido and not pedidos:
        nomes = ", ".join(CODIGOS[c] for c in sorted({i["codigo"] for i in guia["itens"]} & EXIGE_PEDIDO))
        motivo = proradis.get("pedido_motivo") or f"Falta o pedido do dentista (encaminhamento) exigido para {nomes}."
        return Decisao(SEM_PEDIDO, responsavel=proradis.get("pedido_responsavel") or "Clínica",
                       motivo=motivo)

    arquivos = entregaveis + pedidos
    return Decisao(AUTO, anexar=arquivos)


# ── Arquivo aceito pelo portal ───────────────────────────────────────────────
def arquivo_aceito(caminho: str) -> tuple[bool, str]:
    ext = os.path.splitext(caminho)[1].lower()
    if ext not in EXTENSOES_OK:
        return False, f"formato {ext or '?'} não aceito (só GIF/JPG/PNG)"
    tam = os.path.getsize(caminho)
    if tam > LIMITE_BYTES:
        return False, f"{tam // 1024} KB acima do limite de 2 MB"
    return True, ""


def adequar_arquivo(caminho: str, destino_dir: str) -> str:
    """Devolve um caminho aceito pelo portal. Imagem grande vira JPEG menor
    (qualidade e depois resolução). Não altera o conteúdo clínico: só compressão.
    Lança ValueError se não der para adequar (ex.: PDF)."""
    ok, _ = arquivo_aceito(caminho)
    if ok:
        return caminho
    from PIL import Image
    try:
        im = Image.open(caminho)
        im.load()
    except Exception as e:
        raise ValueError(f"não é imagem ({os.path.basename(caminho)}): {e}")
    im = im.convert("RGB")
    base = os.path.splitext(os.path.basename(caminho))[0]
    saida = os.path.join(destino_dir, base + ".jpg")
    escala = 1.0
    for _ in range(8):
        alvo = im if escala == 1.0 else im.resize((int(im.width * escala), int(im.height * escala)))
        for q in (90, 80, 70, 60):
            buf = io.BytesIO()
            alvo.save(buf, "JPEG", quality=q, optimize=True)
            if buf.tell() <= LIMITE_BYTES:
                with open(saida, "wb") as f:
                    f.write(buf.getvalue())
                return saida
        escala *= 0.8
    raise ValueError(f"não coube em 2 MB: {os.path.basename(caminho)}")
