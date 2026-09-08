"""conferencia.py — pergunta ao PORTAL se a guia faturada ficou completa.

O sistema confiava no proprio relato: upload OK = faturada. Ninguem voltava ao
convenio para conferir. A diferenca entre "o robo diz que anexou" e "o convenio
confirma que esta la" foi o que produziu as 54 guias vencidas descobertas em 29/08
— o laudo existia no PRORADIS e a guia seguia incompleta porque o robo tinha
parado de olhar.

SO LEITURA: /v1/gto/imagens, a mesma fonte que a esteira trata como autoritativa.
Nao anexa, nao decide, nao muda nada.
"""
import os
from collections import defaultdict

from sqlalchemy import text

import db
from config import PLANOS
from esteira import _anexos_via_api, _falta_no_portal
from extrator_odontoprev import (abrir_consultar_gtos, consultar_periodo,
                                 login_odonto)
from solicitacao_utils import canon_exames


def conferivel(item) -> bool:
    """Esta linha da execucao deve ser conferida contra o portal?

    Duas familias entram, por motivos diferentes:

    - `auto`/`justificativa` FATURADAS: o robo diz que anexou, e "o robo diz" nao
      e prova. Foi para isto que a conferencia nasceu.
    - `ja_anexada` NAO faturada: a guia tem documento, mas `_falta_no_portal`
      apontou que ele nao cobre o que ela autoriza. Ate 08/09 estas viravam so
      mensagem — caso ANA PAULA (196933166), que repetiu "nao ha imagem do exame"
      em seis rodadas seguidas sem ninguem poder agir, porque a trava
      anti-duplicacao impede a esteira de escrever em guia que ja tem anexo.
      `completar.py` sabe fazer isso com seguranca (7 dias, so o exame ausente,
      `max_antes` da contagem viva); faltava a guia chegar ate ele.

    Fica de fora `ja_anexada` faturada (a documentacao da clinica ja cobre — nao
    gastar chamada nem risco), `auto` nao faturada (anexacao que falhou volta pela
    esteira, com o retry) e as pendencias de verdade (sem_laudo, sem_solicitacao,
    sem_exame): nelas nao ha o que completar, falta o insumo."""
    cat = (item or {}).get("categoria")
    fat = bool((item or {}).get("faturado"))
    if cat in ("auto", "justificativa"):
        return fat
    if cat == "ja_anexada":
        return not fat
    return False


def faturadas_desde(momento):
    """Guias da rodada que precisam ser conferidas contra o portal.

    O SQL so junta os CANDIDATOS; quem decide e `conferivel`, para a regra ficar
    em Python e testavel. Antes o filtro morava aqui dentro e so trazia o que o
    ROBO anexou — por isso a guia travada em `ja_anexada` incompleta nunca chegava
    na conferencia (caso ANA PAULA)."""
    sql = """select distinct on (i.gto) i.gto, i.paciente, x.dia, x.conta,
                    coalesce(i.exames_gto,'') eg, i.categoria, i.faturado
             from execucao_itens i join execucoes x on x.id = i.execucao_id
             where x.criado_em >= :m
               and i.categoria in ('auto','justificativa','ja_anexada')
             order by i.gto, x.criado_em desc"""
    with db.engine.connect() as c:
        linhas = [dict(r) for r in c.execute(text(sql), {"m": momento}).mappings()]
    return [x for x in linhas if conferivel(x)]


def conferir(itens, pw=None, log=None):
    """(completas, incompletas, nao_conferidas).

    `incompletas` = [{gto, paciente, dia, conta, falta, anexos}].
    NUNCA levanta: guia que nao deu para ler entra em `nao_conferidas`, jamais em
    `completas` — silencio nao vira aprovacao."""
    _log = log or (lambda m: None)
    completas, incompletas, nao = [], [], []
    if not itens:
        return completas, incompletas, nao
    por_conta = defaultdict(list)
    for a in itens:
        por_conta[a["conta"]].append(a)

    from playwright.sync_api import sync_playwright
    ctx_mgr = None
    if pw is None:
        ctx_mgr = sync_playwright()
        pw = ctx_mgr.__enter__()
    try:
        for conta, lista in sorted(por_conta.items()):
            unid = PLANOS.get(conta, {}).get("label", conta)
            bearer = {"v": None}
            try:
                br, ctx, pg = login_odonto(pw, conta, db.get_portal_senha(conta))
            except Exception as e:
                _log(f"[conf] {unid}: login falhou — {str(e)[:90]}")
                nao += [(a, "login") for a in lista]
                continue
            try:
                ctx.on("request", lambda r: bearer.__setitem__(
                    "v", r.headers.get("authorization"))
                    if "credenciado.odontoprev.com.br" in r.url
                    and (r.headers.get("authorization") or "").lower().startswith("bearer")
                    else None)
                try:
                    abrir_consultar_gtos(pg)
                    consultar_periodo(pg, lista[0]["dia"])
                    pg.wait_for_timeout(2500)
                except Exception:
                    pass
                for a in lista:
                    n, nomes, err = _anexos_via_api(bearer["v"], a["gto"])
                    if n < 0:
                        nao.append((a, err or "api"))
                        continue
                    falta = _falta_no_portal(canon_exames(a["eg"]), sorted(nomes))
                    if falta:
                        d = dict(a)
                        d["falta"] = falta
                        d["anexos"] = sorted(nomes)
                        incompletas.append(d)
                        _log(f"[conf] INCOMPLETA {a['gto']} {a['paciente']} — falta {falta}")
                    else:
                        completas.append(a)
            finally:
                try:
                    br.close()
                except Exception:
                    pass
    finally:
        if ctx_mgr is not None:
            try:
                ctx_mgr.__exit__(None, None, None)
            except Exception:
                pass
    return completas, incompletas, nao

_GTO_NO_PORTAL = ("IMG_ASSINADA", "IMAGEMGTO")


def _eh_imagem(nome):
    u = str(nome).upper()
    if any(u.startswith(x) for x in _GTO_NO_PORTAL):
        return False
    if "LAUDO" in u or u.startswith("SOLICITACAO"):
        return False
    return u.endswith((".JPG", ".JPEG", ".PNG")) or "IMAGE" in u


def _exames_com_laudo(nomes):
    """Exames que JA tem laudo na guia. Le nome do robo (LAUDO_<EXAME>_<acc>) e
    nome livre de quem anexa a mao ('Laudo Cefalometrico.pdf')."""
    tem = set()
    for n in nomes or []:
        u = str(n).upper()
        if "LAUDO" in u or "CEPH" in u:
            tem |= canon_exames(str(n))
    return tem


def arquivos_que_faltam(exames_canon, no_portal, disponiveis) -> list:
    """Quais dos `disponiveis` devem subir para completar a guia — comparando por
    TIPO DE EXAME, nunca por nome de arquivo.

    A idempotencia do `upload_arquivos` e por NOME, e quem anexa a mao usa nome
    livre: a guia da JESSICA (196708276) tem 'Laudo Cefalometrico.pdf', que e o
    mesmo exame do 'LAUDO_TELERRADIOGRAFIA_<acc>_CEPH.pdf' do robo. Por nome os dois
    subiriam e a guia ficaria com laudo duplicado — irreversivel, o portal nao
    remove anexo.

    Conservadora nos dois sentidos:
      - exame ja coberto NUNCA sobe de novo;
      - arquivo cujo exame nao da para reconhecer NAO sobe (na duvida, nao arrisca).

    Imagem: so entra quando a guia nao tem NENHUMA. A folha de entrega e composta —
    sem saber o que ha dentro dela, acrescentar outra e arriscar duplicar."""
    from esteira import _DOC_COMPONENTES, _LAUDO_ESPERADO
    exigidos = set()
    for e in (exames_canon or ()):
        exigidos |= (_DOC_COMPONENTES
                     if e in ("documentacao", "documentacao_completa") else {e})
    ja = _exames_com_laudo(no_portal)
    falta_laudo = {e for e in exigidos if e in _LAUDO_ESPERADO and e not in ja}
    tem_imagem = any(_eh_imagem(n) for n in (no_portal or []))
    out = []
    for f in disponiveis or []:
        u = str(f).upper()
        if u.startswith("SOLICITACAO"):
            continue                      # pedido do dentista e outro gate
        if _eh_imagem(f):
            if not tem_imagem:
                out.append(f)
            continue
        if "LAUDO" not in u and "CEPH" not in u:
            continue                      # nao da para saber que exame e -> nao sobe
        ex = canon_exames(str(f))
        if ex & falta_laudo:
            out.append(f)
    return out

