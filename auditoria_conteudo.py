"""Auditoria de CONTEUDO das guias que o robo faturou (10/10).

As outras conferencias olham NOMES de arquivo ("tem LAUDO_*, tem ENTREGA_*"). Esta
abre o que esta de fato anexado no portal e confere contra a propria GTO:
documento de outra pessoa, pedido de outro dentista, exame sem laudo, guia sem
imagem.

Le o CONJUNTO de anexos de cada guia e classifica cada um pelo CONTEUDO. Nunca
atribui conteudo a um nome de arquivo: a posicao (sequencial) do acervo nao
corresponde a ordem da lista de nomes (memoria radiobras-acervo-sequencial,
16/09). PDFs voltam do acervo como a 1a pagina em JPEG.

SO LEITURA: nao anexa, nao muda nada.

Uso:  python auditoria_conteudo.py [--dias 30] [--conta 397950] [--saida pasta]
"""
import argparse
import json
import os
import re
import sys
import time

from solicitacao_utils import canon_exames

# ── veredito (puro, testavel) ─────────────────────────────────────────────────

_PROMPT = """Acima estão os anexos de UMA guia odontológica, indexados ([anexo 0], [anexo 1], ...).
Você é um LEITOR: transcreva, não decida. Para CADA anexo devolva:
- "idx": número do anexo
- "tipo": "gto" (guia TISS/GTO da operadora) | "pedido" (pedido/requisição de exame
  feito por dentista) | "laudo" (página de texto de relatório/laudo radiológico) |
  "imagem" (página com radiografias, tomografia, fotografias clínicas, traçado ou
  análise cefalométrica, modelo) | "documento" (RG/CNH/certidão/carteira) | "outro"
  Cada anexo é UMA página; um laudo de várias páginas aparece como vários anexos.
- "paciente": nome do paciente escrito no anexo ("" se não houver ou ilegível; NÃO invente)
- "dentista": nome do dentista que assina/carimba ("" se não houver)
- "cro": número do CRO, só dígitos ("" se não houver)
- "data": data principal escrita no anexo, "DD/MM/AAAA" ou ""
- "exames": exames pedidos (pedido), laudados (laudo), autorizados (gto) ou mostrados (imagem)
Se o anexo for a GTO, inclua também:
- "profissional_solicitante": nome do campo 17 "Nome do Profissional Solicitante"
  (o DENTISTA; NÃO é o beneficiário, o titular nem o responsável)
- "conselho_numero": número do campo 18 "Número no CRO" (do solicitante), só dígitos
- "campo_49": texto do campo 49 (Observação/Justificativa), "" se vazio
Responda APENAS JSON: {"anexos": [ ... ]}"""


def _ex(v):
    """'exames' pode vir lista ou texto."""
    if isinstance(v, (list, tuple)):
        return " ".join(str(e) for e in v)
    return str(v or "")


_ABREV = {"JR": "JUNIOR", "JUNIO": "JUNIOR", "STOS": "SANTOS", "STO": "SANTO"}


def _toks(n):
    from esteira import normaliza_nome
    return [_ABREV.get(t, t) for t in normaliza_nome(n or "").split()]


def _compat(a, b):
    """_nomes_compat da esteira + abreviacao de papel (Jr, Stos, inicial 'G.'),
    que la e recusa correta mas aqui nao e documento de OUTRA pessoa."""
    from difflib import SequenceMatcher
    from esteira import _nomes_compat
    if not (a and b):
        return False
    if _nomes_compat(a, b):
        return True
    ta, tb = _toks(a), _toks(b)
    if not ta or not tb or SequenceMatcher(None, ta[0], tb[0]).ratio() < 0.8:
        return False
    curto, longo = sorted((ta[1:], tb[1:]), key=len)
    return bool(curto) and all(
        any(u == t or (len(t) == 1 and u.startswith(t)) for u in longo) for t in curto)


def _parente(dent, paciente):
    """A leitura do campo 17 deu nome com sobrenome do PACIENTE: e o titular ou o
    proprio paciente lido no lugar errado, nao o dentista."""
    from esteira import _STOP_NOME
    sob = lambda n: {t for t in _toks(n)[1:] if t not in _STOP_NOME and len(t) > 2}  # noqa: E731
    return bool(sob(dent) & sob(paciente))


def veredito(paciente, exames_gto, leituras, categoria=None, gto_ref=None):
    """(status, motivos). status: OK | FALTA | ERRADO | INCERTO.

    ERRADO = algo anexado que nao devia (documento de outra pessoa, pedido de outro
    dentista). FALTA = algo que devia estar e nao esta. INCERTO = nao deu para
    afirmar (ilegivel). Anexo da clinica entra no conjunto: tambem e problema."""
    from esteira import _dentista_contradiz
    motivos_err, motivos_falta, motivos_inc = [], [], []
    ls = [l for l in (leituras or []) if isinstance(l, dict)]
    gto = next((l for l in ls if l.get("tipo") == "gto"), {})
    if gto_ref is not None:
        # referencia DA ESTEIRA (texto da guia, campo 17/18 e campo 49): mais firme
        # que a leitura da imagem da GTO. Usada na simulacao, onde a GTO nao esta
        # entre os arquivos.
        dent_gto, gto_txt = str(gto_ref.get("dentista") or ""), str(gto_ref.get("texto") or "")
        gto = dict(gto, campo_49=gto_ref.get("campo_49") or gto.get("campo_49"))
    else:
        dent_gto = str(gto.get("profissional_solicitante") or "")
        if _parente(dent_gto, paciente):
            dent_gto = ""                    # leitura do campo 17 nao confiavel
        cro_gto = re.sub(r"\D", "", str(gto.get("conselho_numero") or ""))
        gto_txt = f"{dent_gto} {cro_gto}"

    for l in ls:
        p = str(l.get("paciente") or "").strip()
        if l.get("tipo") in ("pedido", "laudo", "imagem", "documento") and p \
                and len(p.split()) >= 2 and not _compat(p, paciente):
            motivos_err.append(f"{l.get('tipo')} [{l.get('idx')}] no nome de OUTRA pessoa: {p!r}")

    pedidos = [l for l in ls if l.get("tipo") == "pedido"]
    meus = [l for l in pedidos if _compat(l.get("paciente"), paciente)]
    if categoria == "justificativa":
        # campo 49 preenchido DISPENSA o pedido (regra da esteira); confere o campo
        if not str(gto.get("campo_49") or "").strip():
            motivos_inc.append("faturada por justificativa, mas o campo 49 da GTO não foi lido")
    elif not pedidos:
        motivos_falta.append("nenhum pedido do dentista anexado")
    elif not meus:
        if not any(str(l.get("paciente") or "").strip() for l in pedidos):
            motivos_inc.append("pedido com nome do paciente ilegível")
    contra = [l for l in meus if dent_gto and _dentista_contradiz(
        {"dentista_lido": l.get("dentista"), "cro_lido": l.get("cro")}, dent_gto, gto_txt)]
    # outro pedido do paciente com o dentista CERTO tambem anexado: o extra nao
    # derruba a guia (LEILA 197392905: Roseana + Fabielle)
    if contra and len(contra) < len(meus):
        contra = []
    for l in contra:
        motivos_err.append(f"pedido [{l.get('idx')}] assinado por OUTRO dentista: "
                           f"{l.get('dentista')!r} (guia: {dent_gto!r})")

    alvo = set(canon_exames(_ex(exames_gto)))
    laudos = [l for l in ls if l.get("tipo") == "laudo"]
    cobertos = set()
    for l in laudos:
        cobertos |= set(canon_exames(_ex(l.get("exames"))))
    if "panoramica" in cobertos:
        # o laudo da panoramica traz periapical e interproximal dentro (dono, 10/10)
        cobertos |= {"periapical", "interproximal"}
    if alvo and not laudos:
        motivos_falta.append("nenhum laudo anexado")
    elif alvo:
        sem = sorted(alvo - cobertos - {"modelos", "fotografias", "documentacao",
                                        "documentacao_completa"})
        if sem:
            motivos_inc.append(f"exame(s) sem laudo identificado: {sem}")
    if alvo and not any(l.get("tipo") == "imagem" for l in ls):
        motivos_inc.append("nenhuma folha de imagem identificada (PDF pode vir só a 1ª página)")

    if motivos_err:
        return "ERRADO", motivos_err + motivos_falta + motivos_inc
    if motivos_falta:
        return "FALTA", motivos_falta + motivos_inc
    if motivos_inc:
        return "INCERTO", motivos_inc
    return "OK", []


# ── coleta (portal, so leitura) ───────────────────────────────────────────────

def guias_faturadas(dias, conta=None):
    import db
    from sqlalchemy import text
    sql = """select distinct on (i.gto) i.gto, i.paciente, x.dia, x.conta,
                    coalesce(i.exames_gto,'') eg, i.categoria
             from execucao_itens i join execucoes x on x.id = i.execucao_id
             where x.criado_em >= now() - make_interval(days => :d)
               and x.dry_run = false and i.faturado
               and i.categoria in ('auto','justificativa')
               and x.conta not like 'hapvida:%'
               and (:c is null or x.conta = :c)
             order by i.gto, x.criado_em desc"""
    with db.engine.connect() as c:
        return [dict(r) for r in c.execute(text(sql), {"d": dias, "c": conta}).mappings()]


def _ler(gem, blobs):
    from google.genai import types
    import esteira
    contents = []
    for i, (b, m) in enumerate(blobs):
        contents.append(f"[anexo {i}]")
        contents.append(types.Part.from_bytes(data=b, mime_type=m))
    contents.append(_PROMPT)
    for tent in range(3):                # JSON cortado acontece; nova chamada resolve
        r = gem.models.generate_content(model=esteira._GEM_MODEL, contents=contents,
                                        config=esteira._gem_cfg())
        t = re.sub(r"^```json|^```|```$", "", (r.text or "").strip(), flags=re.M).strip()
        try:
            d = json.loads(t)
            return (d.get("anexos") if isinstance(d, dict) else d) or []
        except ValueError:
            if tent == 2:
                raise


def auditar(dias=30, conta=None, saida=".", log=print):
    import requests
    import db
    import esteira
    from config import PLANOS
    from extrator_odontoprev import abrir_consultar_gtos, consultar_periodo, login_odonto
    from google import genai
    from playwright.sync_api import sync_playwright

    gem = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    guias = guias_faturadas(dias, conta)
    arq = os.path.join(saida, "auditoria_conteudo.jsonl")
    feitas = set()
    if os.path.exists(arq):
        for ln in open(arq, encoding="utf-8"):
            try:
                r0 = json.loads(ln)
                if r0.get("status") != "NAO_AUDITADA":
                    feitas.add(r0["gto"])
            except Exception:
                pass
    pend = [g for g in guias if g["gto"] not in feitas]
    log(f"{len(guias)} guias faturadas; {len(pend)} a auditar")
    por_conta = {}
    for g in pend:
        por_conta.setdefault(g["conta"], []).append(g)
    with sync_playwright() as pw:
        for ct, lista in sorted(por_conta.items()):
            unid = PLANOS.get(ct, {}).get("label", ct)
            sessao = {"br": None}

            def _logar(dia):
                # JWT expira no meio do bloco (~20 min): relogin quando vier 401
                if sessao["br"]:
                    try:
                        sessao["br"].close()
                    except Exception:
                        pass
                bearer = {"v": None}
                br, ctx, pg = login_odonto(pw, ct, db.get_portal_senha(ct))
                ctx.on("request", lambda r: bearer.__setitem__("v", r.headers.get("authorization"))
                       if "odontoprev.com.br" in r.url
                       and (r.headers.get("authorization") or "").lower().startswith("bearer")
                       else None)
                try:
                    abrir_consultar_gtos(pg)
                    consultar_periodo(pg, dia)
                    pg.wait_for_timeout(2500)
                except Exception:
                    pass
                sess = requests.Session()
                sess.headers.update({"Authorization": bearer["v"] or "", "User-Agent": "Mozilla/5.0",
                                     "Origin": "https://credenciado.odontoprev.com.br",
                                     "Referer": "https://credenciado.odontoprev.com.br/"})
                sessao.update(br=br, sess=sess)

            for ini in range(0, len(lista), 60):          # relogin a cada 60 (JWT)
                bloco = lista[ini:ini + 60]
                _logar(bloco[0]["dia"])
                for g in bloco:
                    reg = {"gto": g["gto"], "paciente": g["paciente"], "dia": g["dia"],
                           "conta": ct, "unidade": unid, "exames_gto": g["eg"]}
                    try:
                        url = f"{esteira._ODO_API}/v1/gto/imagens?numeroFicha={g['gto']}"
                        r = sessao["sess"].get(url, timeout=25)
                        if r.status_code == 401:
                            _logar(g["dia"])
                            r = sessao["sess"].get(url, timeout=25)
                        sess = sessao["sess"]
                        lst = r.json() if r.status_code == 200 else None
                        if not isinstance(lst, list):
                            raise RuntimeError(f"lista de anexos HTTP {r.status_code}")
                        reg["nomes"] = [str(i.get("nomeArquivo", "")) for i in lst if isinstance(i, dict)]
                        # O acervo numera PAGINAS, nao arquivos (medido 10/10: WENDEL
                        # 197121795, 9 arquivos = 14 paginas). Baixar so ate o numero
                        # de arquivos perdia as ultimas paginas (ali estava o pedido).
                        blobs = []
                        for seq in range(1, 61):
                            b, m = esteira._baixar_anexo_portal(sess, g["gto"], seq)
                            if not b:
                                break
                            blobs.append((b, m))
                        reg["baixados"] = len(blobs)
                        if not blobs:
                            raise RuntimeError("nenhum anexo baixado")
                        leit = _ler(gem, blobs)
                        reg["leituras"] = leit
                        reg["categoria"] = g.get("categoria")
                        reg["status"], reg["motivos"] = veredito(
                            g["paciente"], g["eg"], leit, g.get("categoria"))
                    except Exception as e:
                        reg["status"], reg["motivos"] = "NAO_AUDITADA", [str(e)[:160]]
                    with open(arq, "a", encoding="utf-8") as f:
                        f.write(json.dumps(reg, ensure_ascii=False) + "\n")
                    log(f"[{unid}] {g['gto']} {g['paciente'][:28]:28} {reg['status']}"
                        + (f" | {'; '.join(reg['motivos'])[:160]}" if reg["motivos"] else ""))
            try:
                sessao["br"].close()
            except Exception:
                pass
    return arq


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=30)
    ap.add_argument("--conta")
    ap.add_argument("--saida", default=".")
    ap.add_argument("--limite", type=int, default=0)
    a = ap.parse_args()
    if a.limite:
        _orig = guias_faturadas
        guias_faturadas = lambda d, c=None: _orig(d, c)[:a.limite]  # noqa: E731
    auditar(a.dias, a.conta, a.saida)
