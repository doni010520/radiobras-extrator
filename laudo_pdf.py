"""PDF do laudo que volta EM BRANCO — detectar e mandar REGERAR.

O SmartRIS gera o PDF do laudo **on-demand**: imprime a página web via Chromium.
Quando o servidor responde antes de a página terminar de carregar, volta um PDF de
**~857 bytes, totalmente vazio** — de forma intermitente. **O laudo existe**; é só o
PDF que não renderizou.

Re-baixar (GET em `report/pdf?studies=`) resolve parte dos casos, e isso o
`baixar_laudos` já fazia. O que faltava: quando re-baixar não resolve, é preciso
mandar o servidor **REGERAR** (`report_pan/generate_pdf/<hash>`).

O hash do `openReportPDF` **não serve** para regerar. O hash certo é o do EDITOR do
laudo, que só aparece na tela **Documentação** (`a.prd-btn.report`) — a mesma tela que
o `baixar_imagens` já abre com `study_id`/`schedule_id`.

Duas armadilhas que custaram caro no levantamento em produção (20-21/08/2026) e que
os testes deste módulo travam:

1. **Casar o cartão só pelo pedido.** Um pedido pode ter vários exames (40343842 =
   TELERRADIOGRAFIA + PANORAMICA): casar só pelo número regera a tele — que estava
   boa — e deixa a panorâmica em branco de pé. Sempre **pedido + nome do exame**.
2. **Confundir os chips.** `prd-chip sent-to-central done` é laudo PRONTO.
   `prd-chip sent-to-central color-1` aparece em *todo* cartão e só diz que foi
   enviado à central; `fa-file-text-o` e `fa-eye` também aparecem em exame SEM laudo.

Consequência de negócio: PDF em branco é **falha nossa**, não do radiologista. Sem
esta distinção a guia virava "falta o LAUDO" e ia cobrar do radiologista um laudo que
já estava pronto. Ver `db.eh_nosso`.
"""
import re
import time
import unicodedata

# Abaixo disso o PDF do laudo não tem conteúdo. O branco real são 857 bytes; a régua
# do repasse é 20.000, mas aqui fica em 10.000 para casar com a que o `baixar_laudos`
# já usava — e porque a telerradiografia legítima tem ~26 KB, bem acima das duas.
LIMITE_BRANCO = 10_000

# Espera entre mandar regerar e medir de novo. Os números vêm da medição real dos 28
# PDFs em branco de 18/08: 17 voltaram na 1a, 6 na 2a, 3 na 3a, 1 na 4a e 1 na 5a —
# por isso a 4a e a 5a esperam mais antes de declarar falha.
ESPERAS = (3, 4, 5, 8, 9)
MAX_TENTATIVAS = len(ESPERAS)


def pdf_em_branco(content) -> bool:
    """PDF vazio (ou nem PDF). Conteúdo que não começa com %PDF conta como branco:
    é erro do servidor, nunca um laudo."""
    if not content or content[:4] != b"%PDF":
        return True
    return len(content) < LIMITE_BRANCO


def _norm(t) -> str:
    """Sem acento, maiúsculo, espaços colapsados — o nome do exame na tela vem com
    acento e por extenso ('RADIOGRAFIA PANORÂMICA DIGITAL'), o nosso vem do rótulo."""
    t = unicodedata.normalize("NFKD", str(t or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).upper().strip()


def achar_cartao(html, exame, pedido=""):
    """Acha o cartão do exame na tela Documentação.

    Devolve {"hash", "done"} do cartão certo, {"ambiguo": True, "hash": None} quando
    há mais de um candidato indistinguível, ou None quando não há cartão.

    Espelha o algoritmo verificado em produção: para cada `a.prd-btn.report`, sobe
    até 8 ancestrais procurando um container cujo texto contenha o exame (e o pedido,
    quando informado); o **menor** container que casa é o cartão."""
    from bs4 import BeautifulSoup
    doc = BeautifulSoup(html or "", "lxml")
    alvo_ex, alvo_pd = _norm(exame), _norm(pedido)
    if not alvo_ex:
        return None
    candidatos = []
    for el in doc.select("a.prd-btn.report"):
        q, achou = el.parent, None
        for _ in range(8):
            if q is None:
                break
            t = _norm(q.get_text(" ", strip=True))
            if alvo_ex in t and (not alvo_pd or alvo_pd in t):
                achou = (len(t), q)
                break                       # o 1o de baixo pra cima = o menor
            q = q.parent
        if not achou:
            continue
        _tam, cont = achou
        candidatos.append({
            "tam": _tam,
            "hash": (el.get("href") or "").rstrip("/").split("/")[-1] or None,
            # SÓ 'done' prova laudo pronto — 'color-1' aparece em todo cartão
            "done": cont.select_one(".prd-chip.sent-to-central.done") is not None,
        })
    if not candidatos:
        return None
    candidatos.sort(key=lambda c: c["tam"])
    menor = candidatos[0]
    empatados = [c for c in candidatos
                 if c["tam"] == menor["tam"] and c["hash"] != menor["hash"]]
    if empatados:
        # Regerar o cartão errado deixa o certo em branco e gasta tentativa. Na
        # dúvida, não mexe: vira falha nossa e o dono decide.
        return {"ambiguo": True, "hash": None, "done": menor["done"]}
    return {"hash": menor["hash"], "done": menor["done"], "ambiguo": False}


# ⚠️ ESTE POST TEM QUE SAIR DE DENTRO DO NAVEGADOR. Verificado ao vivo em 22/08:
# `requests.post` com os cookies copiados do contexto devolve a PÁGINA DE LOGIN
# (4.595 bytes, zero cartões), enquanto o mesmo POST por `fetch` same-origin devolve
# a tela de verdade (267 KB, 6 cartões). O GET de `report/pdf?studies=` funciona por
# requests — o problema é só com este POST. É por isso que `recuperar_pdf` aceita
# `abrir_doc`/`regerar` injetados: em produção quem chama passa o `page.evaluate`.
_JS_DOC = """async ([s, sc]) => {
    const body = new URLSearchParams({study_id: s, schedule_id: sc});
    const r = await fetch('/ris/reports_doc', {method: 'POST', body,
        credentials: 'include', headers: {'X-Requested-With': 'XMLHttpRequest'}});
    return await r.text();
}"""

_JS_GERAR = """async (u) => {
    const r = await fetch(u, {credentials: 'include', cache: 'no-store'});
    return r.status;
}"""


def abrir_documentacao_no_browser(page, study_id, schedule_id):
    """HTML da tela Documentação, buscado DE DENTRO da página logada."""
    return page.evaluate(_JS_DOC, [str(study_id), str(schedule_id)]) or ""


def regerar_no_browser(page, base, hash_editor):
    """Manda regerar de dentro da página logada (mesmo motivo do POST acima)."""
    st = page.evaluate(_JS_GERAR, base + "/report_pan/generate_pdf/" + str(hash_editor))
    return 200 <= int(st) < 300


def _parece_login(html) -> bool:
    """A resposta é a tela de login? Marcadores colhidos da resposta real de 22/08."""
    h = str(html or "")
    return ("login-box" in h or "css/login.css" in h) and "prd-btn" not in h


def abrir_documentacao(sess, base, study_id, schedule_id):
    """Versão por `requests` — mantida para teste e para caminhos que já tenham uma
    sessão válida. Em produção use `abrir_documentacao_no_browser` (ver nota acima)."""
    r = sess.post(base + "/reports_doc",
                  data={"study_id": str(study_id), "schedule_id": str(schedule_id)},
                  timeout=60)
    return r.text or ""


def mandar_regerar(sess, base, hash_editor):
    """Pede ao servidor para regerar o PDF do laudo. Responde 204 quando aceita."""
    r = sess.get(base + "/report_pan/generate_pdf/" + str(hash_editor), timeout=60)
    return 200 <= r.status_code < 300


def recuperar_pdf(sess, base, doc, exame, token_pdf, pedido="", log=None, _sleep=None,
                  abrir_doc=None, regerar=None):
    """Tenta transformar um PDF em branco num PDF de verdade.

    Devolve {"ok", "content", "bytes", "tentativas", "motivo"}. `motivo` só vem
    preenchido quando NÃO deu — e o texto já sai no vocabulário certo pra
    classificação: "falha técnica" quando o laudo existe, "sem laudo" quando não."""
    _log = log or (lambda m: None)
    _dorme = _sleep or time.sleep
    vazio = {"ok": False, "content": b"", "bytes": 0, "tentativas": 0}
    if not doc or not doc.get("study_id"):
        return dict(vazio, motivo="sem token da tela Documentação para regerar o PDF")
    _abrir = abrir_doc or (lambda: abrir_documentacao(
        sess, base, doc["study_id"], doc.get("schedule_id")))
    _gerar = regerar or (lambda h: mandar_regerar(sess, base, h))
    try:
        html = _abrir()
        if _parece_login(html):
            # o erro exato que o diagnóstico de 22/08 pegou: veio a tela de LOGIN, não
            # a Documentação. Dizer isso é melhor do que concluir "não achei o cartão"
            # — que mandaria investigar o exame quando o problema é a sessão.
            return dict(vazio, motivo="falha técnica: a tela Documentação voltou a "
                                      "página de login (sessão não autenticada)")
    except Exception as e:
        return dict(vazio, motivo="falha técnica ao abrir a Documentação: " + str(e)[:120])
    cartao = achar_cartao(html, exame, pedido)
    if cartao is None and pedido:
        # O número que a esteira carrega é o ACCESSION do PRORADIS, que nem sempre é
        # o "pedido" mostrado na tela. Se casar com os dois não achar nada, cai pro
        # nome do exame sozinho — perder a regeração em silêncio seria pior. A trava
        # de ambiguidade continua protegendo contra regerar o cartão errado.
        cartao = achar_cartao(html, exame)
    if cartao is None:
        return dict(vazio, motivo="falha técnica: não achei o cartão do exame "
                                  + str(exame) + " na Documentação")
    if not cartao.get("done"):
        # Não é PDF em branco: é laudo que ainda não existe. Vai pro radiologista.
        return dict(vazio, motivo="sem laudo emitido ainda")
    if cartao.get("ambiguo"):
        return dict(vazio, motivo="falha técnica: mais de um cartão de " + str(exame)
                                  + " na Documentação e não deu pra saber qual regerar")
    h = cartao["hash"]
    melhor, tam = b"", 0
    for i, espera in enumerate(ESPERAS, start=1):
        try:
            if not _gerar(h):
                _log("[pdf] regerar " + str(exame) + " recusado na tentativa %d" % i)
        except Exception as e:
            _log("[pdf] regerar %s falhou na tentativa %d: %s" % (exame, i, str(e)[:80]))
        _dorme(espera)
        try:
            r = sess.get(base + "/report/pdf?studies=" + str(token_pdf), timeout=60)
            if not pdf_em_branco(r.content):
                _log("[pdf] %s: recuperado na tentativa %d (%dB)"
                     % (exame, i, len(r.content)))
                return {"ok": True, "content": r.content, "bytes": len(r.content),
                        "tentativas": i, "motivo": ""}
            if len(r.content) > tam:
                melhor, tam = r.content, len(r.content)
        except Exception as e:
            _log("[pdf] remedir %s falhou na tentativa %d: %s" % (exame, i, str(e)[:80]))
    return {"ok": False, "content": melhor, "bytes": tam, "tentativas": MAX_TENTATIVAS,
            "motivo": ("falha técnica: o laudo de " + str(exame) + " existe, mas o PDF "
                       "continuou EM BRANCO depois de %d tentativas de regerar "
                       "(falha nossa, não do radiologista)" % MAX_TENTATIVAS)}
