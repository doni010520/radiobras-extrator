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


def _link(caminho: str) -> str:
    base = (os.environ.get("APP_BASE_URL") or "").rstrip("/")
    return (base + caminho) if base else ""


# ── as três mensagens ───────────────────────────────────────────────────────
def avisar_aborto(dia: str, conta: str, erro: str, execucao_id=None, _post=None) -> bool:
    """A execução MORREU inteira (login/proxy/Gemini fora) — o dia todo não faturou.
    Este era o furo mais caro: sem pendência, sem fila e sem ninguém avisado, o dia
    simplesmente não acontecia em silêncio. Vai IMEDIATO."""
    linhas = ["🚨 *RadioBras — a rodada não terminou*",
              "",
              f"*Dia:* {dia}",
              f"*Unidade:* {conta}",
              "",
              "O dia *não faturou* — a execução abortou antes do fim.",
              f"*Erro:* {str(erro or '')[:300]}"]
    if execucao_id:
        url = _link(f"/relatorios/execucao/{execucao_id}/log")
        linhas.append("")
        linhas.append(f"Log: {url}" if url else f"Execução #{execucao_id}")
    linhas += ["", "O sistema vai tentar de novo sozinho."]
    return enviar_whatsapp("\n".join(linhas), _post=_post)


def avisar_falhas_da_rodada(dia: str, conta: str, itens: list, _post=None) -> bool:
    """UMA mensagem por rodada com as guias que falharam por problema nosso — não uma
    por guia. Quando o proxy cai, 30 guias falham juntas: mensagem por guia viraria
    enxurrada e o dono pararia de ler. Já estão todas na fila de retry."""
    itens = [i for i in (itens or []) if i]
    if not itens:
        return False
    n = len(itens)
    linhas = [f"⚠️ *RadioBras — {n} guia(s) com falha nossa*",
              "",
              f"*Dia:* {dia}   *Unidade:* {conta}",
              "Não é falta de documento — é problema do robô. A operação *não* vê "
              "essas guias; o sistema já está re-tentando.",
              ""]
    for i in itens[:20]:
        linhas.append(f"• *{i.get('gto') or '?'}* — {i.get('paciente') or '?'}")
        linhas.append(f"  _{str(i.get('motivo') or '')[:110]}_")
    if n > 20:
        linhas.append(f"… e mais {n - 20}.")
    url = _link("/relatorios/pendencias")
    if url:
        linhas += ["", f"Fila técnica: {url}"]
    return enviar_whatsapp("\n".join(linhas), _post=_post)


def avisar_pausa(motivo: str, guias: int, minutos: int, dia: str = "", conta: str = "",
                 _post=None) -> bool:
    """APAGÃO: o mundo caiu (proxy fora, login não passa). UMA mensagem — não uma por
    guia. Em 22/08 a banda do proxy acabou e você recebeu 13 avisos em 2 minutos, um
    por guia, cada um depois de queimar 6 tentativas. Esta mensagem substitui aquilo:
    diz o que caiu, quantas guias foram poupadas e quando o robô tenta de novo."""
    linhas = ["🛑 *RadioBras — parei o retry: falha geral*",
              "",
              "Não é problema de guia nenhuma — é a infraestrutura.",
              f"*O que houve:* {str(motivo or '')[:220]}"]
    if dia or conta:
        linhas.append(f"*Onde vi:* dia {dia or '?'}, unidade {conta or '?'}")
    linhas += ["",
               f"*{guias} guia(s)* tiveram a tentativa DEVOLVIDA — não gastaram o "
               f"orçamento de retry por causa disso.",
               f"A fila fica parada por *{minutos} min* e volta sozinha. Se ainda "
               f"estiver fora, paro de novo e te aviso.",
               "",
               "Nada foi anexado e nada se perdeu."]
    url = _link("/relatorios/pendencias")
    if url:
        linhas += ["", f"Fila técnica: {url}"]
    return enviar_whatsapp(chr(10).join(linhas), _post=_post)


def avisar_esgotou(gto: str, paciente: str, dia: str, conta: str, motivo: str,
                   tentativas: int, _post=None) -> bool:
    """O try again ACABOU e não recuperou. É a única classe que precisa de você —
    e mesmo assim não volta pro painel do operador (ele não conserta bug nosso)."""
    linhas = ["❗ *RadioBras — o retry não recuperou*",
              "",
              f"*Guia:* {gto}   *Paciente:* {paciente or '?'}",
              f"*Dia:* {dia}   *Unidade:* {conta}",
              f"*Tentativas:* {tentativas}",
              "",
              f"*Último erro:* {str(motivo or '')[:250]}",
              "",
              "Essa precisa de você — o sistema já tentou tudo que podia."]
    url = _link("/relatorios/pendencias")
    if url:
        linhas += ["", f"Fila técnica: {url}"]
    return enviar_whatsapp("\n".join(linhas), _post=_post)
