"""Aviso de FALHA TÉCNICA ao dono, por WhatsApp (uazapi).

Regra do dono (22/08/26): **falha de sistema não é pendência do painel**. Se a guia
não faturou por problema NOSSO (infra, leitura, anexação), a operação da RadioBras
não tem o que fazer com aquilo — quem precisa saber é o dono, e quem resolve é o
próprio sistema, re-tentando (`db.deve_entrar_no_retry` + `esteira.processar_retries`).

Este módulo é **só o canal**. Quem decide o que é falha nossa é `db.eh_nosso`.

Princípio inegociável: **aviso nunca derruba faturamento**. Config faltando, uazapi
fora do ar, resposta estranha — tudo retorna False e a esteira segue. É o mesmo
padrão do `_send_email` (app.py), que também falha quieto.

Envs:
  UAZAPI_HOST         servidor da uazapi (default: https://benitechlab.uazapi.com)
  UAZAPI_TOKEN        token da INSTÂNCIA que envia (não é o admintoken)
  ALERTA_WHATSAPP_TO  quem recebe; vários separados por vírgula (55DDDNUMERO)
  ALERTA_FALHA        "0" desliga o canal (default ligado, mas inerte sem token)
  APP_BASE_URL        raiz pública, p/ o aviso trazer link clicável pro log
"""
import os

_HOST_PADRAO = "https://benitechlab.uazapi.com"
_TIMEOUT = 12


def _ligado() -> bool:
    return os.environ.get("ALERTA_FALHA", "1") != "0"


def _destinos() -> list:
    """Números que recebem. Aceita vários separados por vírgula."""
    return [n.strip() for n in (os.environ.get("ALERTA_WHATSAPP_TO") or "").split(",")
            if n.strip()]


def whatsapp_configurado() -> bool:
    """Tem token de instância E destino? Sem os dois o canal é inerte.
    Exposto pro /api/diag dizer a verdade em vez de prometer aviso que não sai."""
    return bool((os.environ.get("UAZAPI_TOKEN") or "").strip() and _destinos())


def _post_uazapi(url: str, headers: dict, payload: dict) -> bool:
    """POST de verdade. Isolado numa função só pra o teste poder substituir."""
    import requests
    r = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
    return 200 <= r.status_code < 300


def enviar_whatsapp(texto: str, _post=None) -> bool:
    """Manda o texto pra todos os destinos. True se ao menos um saiu.
    NUNCA levanta: quem chama está no meio de uma rodada de faturamento."""
    if not _ligado() or not whatsapp_configurado() or not (texto or "").strip():
        return False
    host = (os.environ.get("UAZAPI_HOST") or _HOST_PADRAO).rstrip("/")
    url = host + "/send/text"
    headers = {"token": (os.environ.get("UAZAPI_TOKEN") or "").strip(),
               "Content-Type": "application/json"}
    post = _post or _post_uazapi
    enviou = False
    for numero in _destinos():
        try:
            if post(url, headers, {"number": numero, "text": texto}):
                enviou = True
        except Exception as e:                      # rede, DNS, timeout, json...
            print(f"[notificador] falhou p/ {numero}: {type(e).__name__}: "
                  f"{str(e)[:80]}", flush=True)
    return enviou


# ── deixar a mensagem legivel para quem le no celular as 6h ─────────────────
# Feedback do dono (23/08), depois de 18 alertas: "esses alertas estao confusos, eu
# nao estou entendendo muita coisa a partir deles". Os tres defeitos eram: unidade
# como CODIGO (397950), oito guias com a MESMA causa viram oito paragrafos do texto
# cru do robo, e nenhuma indicacao de se ele precisa levantar da cama ou nao.

def _nome_unidade(conta) -> str:
    """397950 -> 'RedeUna — Tancredo'. Codigo nao diz nada a quem le no celular."""
    if not conta:
        return "?"
    try:
        from config import PLANOS
        p = PLANOS.get(str(conta))
        return (p or {}).get("label") or str(conta)
    except Exception:
        return str(conta)


# Causa em UMA linha, em portugues. A ordem importa: o primeiro que casa vence.
_CAUSAS = [
    ("o portal não abriu a guia", r"Linha da GTO .* n[ãa]o encontrada"),
    ("o acesso ao portal venceu no meio da rodada", r"Jwt is expired|jwt.{0,6}expir"),
    ("o proxy do OdontoPrev caiu", r"ProxyError|Max retries exceeded"),
    ("o campo de upload não apareceu na guia", r"input\[type=file\].*n[ãa]o encontrado"),
    ("a leitura dos documentos falhou", r"gemini\s*:|falha t[ée]cnica na leitura"),
    ("a leitura ficou sem crédito", r"cr[ée]ditos da API|leitura autom[áa]tica ficou indispon"),
    ("não deu para contar os anexos da guia", r"n[ãa]o consegui ler quantos anexos"),
]


def _resumir_causa(motivo) -> str:
    """Uma linha dizendo O QUE aconteceu. Causa nao mapeada aparece como veio,
    cortada — nunca inventar um resumo bonito para algo que nao entendi."""
    import re as _re
    m = str(motivo or "")
    for rotulo, padrao in _CAUSAS:
        if _re.search(padrao, m, _re.I):
            return rotulo
    return _re.sub(r"\s+", " ", m).strip()[:90]


def _agrupar_por_causa(itens) -> list:
    """[(causa, [guias])], maior grupo primeiro.

    No incidente #613 foram 8 guias com a MESMA causa — viravam 8 paragrafos do
    texto interno. Agrupadas, viram uma linha: "8 guias — o portal nao abriu a
    guia"."""
    por = {}
    for i in (itens or []):
        if not i:
            continue
        por.setdefault(_resumir_causa(i.get("motivo")), []).append(i)
    return sorted(por.items(), key=lambda kv: -len(kv[1]))


def _link(caminho: str) -> str:
    base = (os.environ.get("APP_BASE_URL") or "").rstrip("/")
    return (base + caminho) if base else ""


# ── as três mensagens ───────────────────────────────────────────────────────
def avisar_aborto(dia: str, conta: str, erro: str, execucao_id=None, _post=None) -> bool:
    """A rodada MORREU inteira — o dia todo não faturou. Vai imediato.

    Este era o furo mais caro: sem pendência, sem fila e sem ninguém avisado, o dia
    simplesmente não acontecia em silêncio."""
    linhas = ["🚨 *RadioBras — o dia não faturou*",
              "",
              f"{_nome_unidade(conta)} · dia {dia}",
              "",
              f"*O que houve:* {_resumir_causa(erro)}",
              "",
              "A rodada morreu antes do fim — nenhuma guia desse dia foi processada.",
              "O robô vai tentar de novo sozinho."]
    if execucao_id:
        url = _link(f"/relatorios/execucao/{execucao_id}/log")
        linhas += ["", (f"Log: {url}" if url else f"Execução #{execucao_id}")]
    return enviar_whatsapp(chr(10).join(linhas), _post=_post)


def avisar_falhas_da_rodada(dia: str, conta: str, itens: list, _post=None) -> bool:
    """UMA mensagem por rodada, agrupada por CAUSA.

    Reescrita em 23/08 com o feedback do dono ("esses alertas estão confusos").
    Antes: 8 guias com a mesma causa viravam 8 parágrafos do texto interno do robô,
    a unidade era o código `397950` e não dizia se ele precisava fazer algo."""
    itens = [i for i in (itens or []) if i]
    if not itens:
        return False
    n = len(itens)
    grupos = _agrupar_por_causa(itens)
    linhas = [f"⚠️ *RadioBras — {n} guia(s) não faturaram*",
              "",
              f"{_nome_unidade(conta)} · dia {dia}",
              "",
              "*O que houve:*"]
    for causa, guias in grupos:
        linhas.append(f"• {len(guias)} — {causa}")
    linhas += ["",
               "*Você não precisa fazer nada agora:* o robô re-tenta sozinho.",
               "A operação da RadioBras não vê nenhuma dessas guias."]
    # nomes: ajudam a reconhecer, mas sem virar parede de texto
    nomes = [str(i.get("paciente") or i.get("gto") or "?").split(" ")[0].title()
             for i in itens]
    vistos, curtos = set(), []
    for x in nomes:
        if x not in vistos:
            vistos.add(x)
            curtos.append(x)
    if curtos:
        linhas += ["", "_Pacientes: " + ", ".join(curtos[:6])
                   + (f" (+{len(curtos) - 6})" if len(curtos) > 6 else "") + "_"]
    url = _link("/tecnico")
    if url:
        linhas += ["", f"Detalhe: {url}"]
    return enviar_whatsapp(chr(10).join(linhas), _post=_post)


def avisar_esgotou(gto: str, paciente: str, dia: str, conta: str, motivo: str,
                   tentativas: int, _post=None) -> bool:
    """Desisti de uma guia. É a única mensagem que pede ação dele."""
    linhas = ["❗ *RadioBras — desisti de uma guia*",
              "",
              f"*{paciente or 'paciente ?'}* · guia {gto}",
              f"{_nome_unidade(conta)} · dia {dia}",
              "",
              f"*Motivo:* {_resumir_causa(motivo)}",
              f"Tentei {tentativas} vezes ao longo de ~5 horas e não passou.",
              "",
              "*Essa precisa de você* — o robô já fez tudo que podia."]
    url = _link("/tecnico")
    if url:
        linhas += ["", f"Detalhe: {url}"]
    return enviar_whatsapp(chr(10).join(linhas), _post=_post)
