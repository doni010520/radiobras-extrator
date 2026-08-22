"""PDF do laudo que volta EM BRANCO: o laudo existe, o PDF e que nao renderizou.

O SmartRIS gera o PDF on-demand (imprime a pagina web via Chromium). Quando o
servidor responde antes de a pagina carregar, volta um PDF de ~857 bytes VAZIO.
Re-BAIXAR nao resolve sempre — as vezes e preciso mandar REGERAR
(`report_pan/generate_pdf/<hash>`), e o hash e o do EDITOR, que so existe na tela
Documentacao (`a.prd-btn.report`), NAO o do `openReportPDF`.

Isso e falha NOSSA (nao do radiologista): o laudo esta pronto. Ver db.eh_nosso.
Estrutura de HTML conforme o repasse verificado em producao (20-21/08/2026)."""
import laudo_pdf as lp


# ── HTML de exemplo: UM pedido com DOIS exames (a armadilha do repasse) ──────
_DOC_HTML = """
<html><body>
 <div class="grupo">
  <div class="prd-card">
    <div class="prd-head">Pedido 40343842 &mdash; TELERRADIOGRAFIA</div>
    <span class="prd-chip sent-to-central color-1"><i class="fa fa-cloud"></i></span>
    <span class="prd-chip sent-to-central done"><i class="fa fa-check fa-stack-2x"></i></span>
    <a class="prd-btn report" href="/ris/report_pan/produce/HASH_TELE">editar</a>
  </div>
  <div class="prd-card">
    <div class="prd-head">Pedido 40343842 &mdash; PANORAMICA</div>
    <span class="prd-chip sent-to-central color-1"><i class="fa fa-cloud"></i></span>
    <span class="prd-chip sent-to-central done"><i class="fa fa-check fa-stack-2x"></i></span>
    <a class="prd-btn report" href="/ris/report_pan/produce/HASH_PAN">editar</a>
  </div>
 </div>
</body></html>
"""

# mesmo pedido, mas a PANORAMICA ainda NAO tem laudo (so o chip generico color-1)
_DOC_SEM_LAUDO = """
<html><body>
  <div class="prd-card">
    <div class="prd-head">Pedido 40343842 &mdash; PANORAMICA</div>
    <span class="prd-chip sent-to-central color-1"><i class="fa fa-cloud"></i></span>
    <i class="fa fa-file-text-o"></i><i class="fa fa-eye"></i>
    <a class="prd-btn report" href="/ris/report_pan/produce/HASH_PAN">editar</a>
  </div>
</body></html>
"""


# ── deteccao do PDF em branco ───────────────────────────────────────────────
def test_857_bytes_e_branco():
    assert lp.pdf_em_branco(b"%PDF" + b"x" * 853) is True


def test_laudo_normal_nao_e_branco():
    assert lp.pdf_em_branco(b"%PDF" + b"x" * 120_000) is False


def test_ceph_de_26kb_e_legitimo_nao_regerar():
    # o repasse avisa: ~26 KB e telerradiografia/cefalometria de verdade
    assert lp.pdf_em_branco(b"%PDF" + b"x" * 26_000) is False


def test_conteudo_que_nem_e_pdf_conta_como_branco():
    assert lp.pdf_em_branco(b"<html>erro 500</html>") is True
    assert lp.pdf_em_branco(b"") is True


# ── achar o cartao certo na tela Documentacao ───────────────────────────────
def test_acha_o_hash_do_exame_certo_e_nao_do_vizinho():
    # A armadilha nº1 do repasse: casar so pelo pedido regerava a TELE (que estava
    # boa) e deixava a PANORAMICA em branco de pe.
    c = lp.achar_cartao(_DOC_HTML, exame="PANORAMICA", pedido="40343842")
    assert c["hash"] == "HASH_PAN"
    c2 = lp.achar_cartao(_DOC_HTML, exame="TELERRADIOGRAFIA", pedido="40343842")
    assert c2["hash"] == "HASH_TELE"


def test_reconhece_que_o_laudo_existe_pelo_chip_done():
    c = lp.achar_cartao(_DOC_HTML, exame="PANORAMICA", pedido="40343842")
    assert c["done"] is True


def test_chip_generico_nao_e_laudo_pronto():
    # armadilha nº2: 'sent-to-central color-1' aparece em TODO cartao; fa-file-text-o
    # e fa-eye tambem aparecem em exame sem laudo. So 'done' vale.
    c = lp.achar_cartao(_DOC_SEM_LAUDO, exame="PANORAMICA", pedido="40343842")
    assert c["done"] is False


def test_exame_com_acento_ou_nome_longo_ainda_casa():
    html = _DOC_HTML.replace("PANORAMICA", "RADIOGRAFIA PANORÂMICA DIGITAL")
    c = lp.achar_cartao(html, exame="PANORAMICA", pedido="40343842")
    assert c["hash"] == "HASH_PAN"


def test_sem_cartao_devolve_none():
    assert lp.achar_cartao("<html><body>nada</body></html>", exame="PANORAMICA") is None


def test_ambiguo_nao_regenera():
    # dois cartoes iguais pro mesmo exame: nao da pra saber qual — melhor NAO mexer
    # (regerar o errado deixa o certo em branco e gasta uma tentativa).
    html = _DOC_HTML.replace("HASH_TELE", "HASH_X").replace("TELERRADIOGRAFIA", "PANORAMICA")
    c = lp.achar_cartao(html, exame="PANORAMICA", pedido="40343842")
    assert c is not None and c.get("ambiguo") is True and c.get("hash") is None


# ── orquestracao: mandar regerar e medir de novo ────────────────────────────
class _Resp:
    def __init__(self, content=b"", text="", status=200):
        self.content, self.text, self.status_code = content, text, status


class _Sess:
    """Sessao falsa. `pdfs` e a sequencia devolvida pelos GET de report/pdf."""
    def __init__(self, html, pdfs):
        self.html, self.pdfs, self.gets, self.geracoes = html, list(pdfs), [], []

    def post(self, url, data=None, timeout=None):
        return _Resp(text=self.html)

    def get(self, url, timeout=None):
        if "generate_pdf" in url:
            self.geracoes.append(url)
            return _Resp(status=204)
        self.gets.append(url)
        return _Resp(content=self.pdfs.pop(0) if self.pdfs else b"")


_BRANCO = b"%PDF" + b"x" * 853
_CHEIO = b"%PDF" + b"x" * 120_000
_DOC = {"study_id": "S1", "schedule_id": "H1"}
_BASE = "https://radiobras.smartris.com.br/ris"


def _rec(sess, exame="PANORAMICA"):
    return lp.recuperar_pdf(sess, _BASE, _DOC, exame, "TOKPDF",
                            pedido="40343842", _sleep=lambda s: None)


def test_recupera_na_segunda_tentativa():
    sess = _Sess(_DOC_HTML, [_BRANCO, _CHEIO])
    r = _rec(sess)
    assert r["ok"] is True and r["tentativas"] == 2
    assert r["bytes"] == len(_CHEIO)
    # mandou regerar com o hash da PANORAMICA, nao o da tele
    assert all("HASH_PAN" in u for u in sess.geracoes)
    assert len(sess.geracoes) == 2


def test_sem_laudo_nao_tenta_regerar():
    # laudo nao existe: e do radiologista, nao nosso. Regerar seria gastar 5 rodadas
    # e ainda cobrar a pessoa errada no fim.
    sess = _Sess(_DOC_SEM_LAUDO, [])
    r = _rec(sess)
    assert r["ok"] is False
    assert r["motivo"] == "sem laudo emitido ainda"
    assert sess.geracoes == []


def test_ambiguo_nao_tenta_regerar():
    html = _DOC_HTML.replace("HASH_TELE", "HASH_X").replace("TELERRADIOGRAFIA", "PANORAMICA")
    sess = _Sess(html, [])
    r = _rec(sess)
    assert r["ok"] is False and sess.geracoes == []
    assert "falha t" in r["motivo"].lower()


def test_desiste_depois_do_teto_e_o_motivo_e_falha_nossa():
    sess = _Sess(_DOC_HTML, [_BRANCO] * lp.MAX_TENTATIVAS)
    r = _rec(sess)
    assert r["ok"] is False
    assert r["tentativas"] == lp.MAX_TENTATIVAS
    assert len(sess.geracoes) == lp.MAX_TENTATIVAS
    # o texto tem que dizer que e NOSSA — senao a guia vira cobranca ao radiologista
    m = r["motivo"].lower()
    assert "falha t" in m and "radiologista" in m


def test_erro_de_rede_no_meio_nao_levanta():
    class _Explode(_Sess):
        def get(self, url, timeout=None):
            if "generate_pdf" in url:
                raise RuntimeError("timeout")
            return _Resp(content=_CHEIO)
    sess = _Explode(_DOC_HTML, [])
    r = _rec(sess)          # a geracao falhou, mas o PDF ja estava bom
    assert r["ok"] is True


# ── o motivo tem que CAIR na classificacao certa ────────────────────────────
# Isto e o ponto de negocio: PDF em branco cobrado do radiologista e cobranca
# errada (o laudo esta pronto). Tem que virar falha NOSSA -> retry + WhatsApp.
def test_motivo_de_pdf_branco_e_falha_nossa():
    from db import eh_nosso, classe_retry, classificar_pendencia
    sess = _Sess(_DOC_HTML, [_BRANCO] * lp.MAX_TENTATIVAS)
    texto = "laudo PANORAMICA: " + _rec(sess)["motivo"]
    assert classe_retry(texto) == "transitorio"      # entra no loop de retry
    assert eh_nosso(texto) is True                    # some do painel
    assert classificar_pendencia(texto)[1] == "Nós"


def test_pdf_branco_de_TELE_nao_vira_esperando_tele():
    # 'esperando_tele' e testado ANTES de falha_tecnica na tabela de grupos; se o
    # texto casasse ali, a guia iria cobrar o tracado cefalometrico do radiologista.
    from db import eh_nosso, classificar_pendencia
    sess = _Sess(_DOC_HTML.replace("PANORAMICA", "OUTRO"), [_BRANCO] * lp.MAX_TENTATIVAS)
    r = lp.recuperar_pdf(sess, _BASE, _DOC, "TELERRADIOGRAFIA", "TOK",
                         pedido="40343842", _sleep=lambda s: None)
    texto = "laudo TELERRADIOGRAFIA: " + r["motivo"]
    assert eh_nosso(texto) is True
    assert classificar_pendencia(texto)[0] == "falha_tecnica"


def test_sem_laudo_continua_sendo_do_radiologista():
    # o caminho antigo NAO pode regredir: laudo que nao existe segue sendo dele
    from db import eh_nosso, classificar_pendencia
    texto = "laudo PANORAMICA nao pronto"
    assert eh_nosso(texto) is False
    assert classificar_pendencia(texto)[1] == "Radiologista"


def test_pedido_que_nao_bate_com_a_tela_ainda_acha_pelo_exame():
    # o numero que temos (accession) pode nao ser o 'pedido' mostrado na tela. Se
    # casar com os dois falhar, cai pro exame sozinho — senao perderiamos a
    # regeracao em silencio, que e o pior dos mundos.
    sess = _Sess(_DOC_HTML, [_CHEIO])
    r = lp.recuperar_pdf(sess, _BASE, _DOC, "PANORAMICA", "TOK",
                         pedido="99999999", _sleep=lambda s: None)
    assert r["ok"] is True
    assert all("HASH_PAN" in u for u in sess.geracoes)


def test_tela_de_login_e_reportada_como_sessao_nao_autenticada():
    """O POST da Documentacao por requests devolve a PAGINA DE LOGIN (verificado ao
    vivo em 22/08: 4.595 bytes, zero cartoes) — so por dentro do navegador vem a tela
    de verdade (267 KB, 6 cartoes). Se isso voltar a acontecer, o motivo tem que
    apontar a SESSAO, nao mandar investigar o exame."""
    login = ('<html><head><link href="https://x/ris/public/css/login.css"></head>'
             '<body><div id="login-box">entrar</div></body></html>')
    sess = _Sess(login, [])
    r = _rec(sess)
    assert r["ok"] is False and sess.geracoes == []
    assert "login" in r["motivo"].lower()


def test_injeta_o_abrir_e_o_regerar_do_navegador():
    # em producao quem chama passa page.evaluate; o modulo nao pode depender do sess
    chamou = {"abrir": 0, "gerar": []}

    def _abrir():
        chamou["abrir"] += 1
        return _DOC_HTML

    def _gerar(h):
        chamou["gerar"].append(h)
        return True

    sess = _Sess("", [_CHEIO])
    r = lp.recuperar_pdf(sess, _BASE, _DOC, "PANORAMICA", "TOK", pedido="40343842",
                         _sleep=lambda s: None, abrir_doc=_abrir, regerar=_gerar)
    assert r["ok"] is True
    assert chamou["abrir"] == 1
    assert chamou["gerar"] == ["HASH_PAN"]
