"""
hapvida_upload.py — Envio de imagem para uma guia no portal Hapvida. ATO REAL.

Sequência (JS de WebDentalAtendimento.pr_anexar_imagens, lida em 13/09/2026):
  1. GET do formulário → campos ocultos novos a cada carga (id_lobstore, cd_imagem_usuario)
     e os extras do storage (v_url, v_subscription, hash...) que o JS acrescenta.
  2. POST multipart em insert_img_audi_odon.php (campo `dados` = arquivo,
     `nome_arq` = "<carteira>-<id_lobstore>.<ext>") → JSON {success}.
     Se falhar, repete em insert_img_aud_od_lob.php → JSON {ret: 1}.
  3. POST form_proced em WebDentalAtendimento.pr_status_upload com pnm_arquivo,
     pstatus=0 e UM `pnu_item` por item da guia a que a imagem pertence.
  Um arquivo por carga de formulário.

NÃO VALIDADO CONTRA O PORTAL: nenhum envio real foi feito até aqui. A primeira
execução real precisa de GO do dono e de conferência no Banco de Imagens.
Trava: só envia com HAPVIDA_ANEXAR_REAL=1 (o fluxo ainda exige dry_run=False).
"""
from __future__ import annotations

import json
import os
import re

from hapvida_decisao import arquivo_aceito

BASE = "https://www.hapvida.com.br/pls/podontow/"


class UploadBloqueado(RuntimeError):
    pass


class UploadFalhou(RuntimeError):
    pass


def extras_do_storage(html_form: str) -> dict:
    """formData.append("chave", "valor") que o JS do formulário injeta no POST."""
    return dict(re.findall(r'formData\.append\("([^"]+)",\s*"([^"]*)"\)', html_form))


def _json(resp) -> dict:
    try:
        return json.loads(resp.content.decode("latin-1", "replace"))
    except ValueError:
        return {}


def enviar_um(http, form: dict, extras: dict, usuario: str, caminho: str, nu_itens: list[int]) -> str:
    """Envia UM arquivo usando um formulário recém-carregado. Devolve o nome gravado."""
    ok, porque = arquivo_aceito(caminho)
    if not ok:
        raise UploadFalhou(f"{os.path.basename(caminho)}: {porque}")
    lob = form["campos_proced"].get("id_lobstore", "")
    if not lob:
        raise UploadFalhou("formulário sem id_lobstore (sessão caiu?)")
    ext = os.path.splitext(caminho)[1].lstrip(".")
    nome = f"{usuario}-{lob}.{ext}"

    dados = {**form["campos_img"], **extras, "nome_arq": nome}
    with open(caminho, "rb") as fh:
        conteudo = fh.read()
    arq = {"dados": (os.path.basename(caminho), conteudo)}

    url_blob = form["acao_img"]
    r = http.post(url_blob, data=dados, files=arq, timeout=120)
    if not (r.ok and _json(r).get("success")):
        url_lob = url_blob.replace("insert_img_audi_odon.php", "insert_img_aud_od_lob.php")
        r = http.post(url_lob, data=dados, files=arq, timeout=120)
        if not (r.ok and _json(r).get("ret") == 1):
            raise UploadFalhou(f"storage recusou {os.path.basename(caminho)} (HTTP {r.status_code})")

    proced = {**form["campos_proced"], "pnm_arquivo": nome, "pstatus": "0"}
    corpo = list(proced.items()) + [("pnu_item", str(n)) for n in nu_itens]
    acao = form["acao_proced"]
    r2 = http.post(acao if acao.startswith("http") else BASE + acao, data=corpo, timeout=60)
    if not r2.ok:
        raise UploadFalhou(f"pr_status_upload HTTP {r2.status_code}")
    return nome


def anexar(portal, usuario: str, guia: str, arquivos: list[str], itens: int) -> list[str]:
    """Anexa cada arquivo a TODOS os itens da guia (o entregável vale para a guia inteira)."""
    if os.environ.get("HAPVIDA_ANEXAR_REAL") != "1":
        raise UploadBloqueado("envio real bloqueado: HAPVIDA_ANEXAR_REAL != 1")
    enviados = []
    for caminho in arquivos:
        html = portal._get("WebDentalAtendimento.pr_anexar_imagens", pCd_Usuario=usuario,
                           pCdPessoa=portal.s.cd_pessoa, pGuia=guia, pmovel="N",
                           pfl_close_on_exit="true")
        from hapvida_portal import parse_form_upload
        form = parse_form_upload(html)
        if form["campos_proced"].get("pguia") != str(guia):
            raise UploadFalhou(f"formulário veio para a guia {form['campos_proced'].get('pguia')}, não {guia}")
        nu = form["itens"] or list(range(1, itens + 1))
        enviados.append(enviar_um(portal.s.http, form, extras_do_storage(html), usuario, caminho, nu))
    return enviados
