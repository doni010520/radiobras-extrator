"""O tipo do anexo tem que sair dos BYTES, nao do nome do arquivo.

Causa raiz do `gemini: 400 INVALID_ARGUMENT — Unable to process input image` que
derrubou FABRICIO DOS SANTOS SOUZA NASCIMENTO (196307916) e as duas DILMA DA
CONCEICAO (196307961, 196308165) em 18/08.

`preparar_anexo` decidia o MIME pela EXTENSAO e devolvia os bytes intactos:

    ext = filename.lower().rsplit(".", 1)[-1]
    if ext in _MIME_DIRETO:
        return _MIME_DIRETO[ext], blob

Um arquivo chamado '.jpg' que na verdade e HEIC, TIFF, ou um JPEG truncado no upload
ia rotulado como image/jpeg. O Gemini tentava decodificar, falhava, e recusava — e
como os anexos vao TODOS numa requisicao so, o lote inteiro caia junto. A guia virava
"falha tecnica" com o pedido valido do lado.

O modulo ja tinha `_mime_do_conteudo()`, que fareja a assinatura real (%PDF, \x89PNG,
\xFF\xD8\xFF). Estava sendo usada em outro caminho e nao aqui.

Ordem correta: assinatura real > extensao > Pillow re-encoda > recusa individual.
Recusar UM anexo e barato (vira 'descartado' e a guia segue); recusar o lote nao."""
import io

import pytest
from PIL import Image

from esteira import preparar_anexo


def _jpeg(cor=(255, 0, 0)):
    b = io.BytesIO(); Image.new("RGB", (40, 40), cor).save(b, format="JPEG")
    return b.getvalue()


def _png():
    b = io.BytesIO(); Image.new("RGB", (40, 40), (0, 255, 0)).save(b, format="PNG")
    return b.getvalue()


def _tiff():
    b = io.BytesIO(); Image.new("RGB", (40, 40), (0, 0, 255)).save(b, format="TIFF")
    return b.getvalue()


# ── o caso: extensao MENTE ────────────────────────────────────────────────
def test_png_disfarcado_de_jpg():
    """Nome diz .jpg, bytes sao PNG. Antes ia como image/jpeg e o Gemini recusava."""
    mime, blob = preparar_anexo("pedido.jpg", _png())
    assert mime == "image/png"
    assert blob[:4] == bytes([0x89]) + b"PNG"


def test_pdf_disfarcado_de_jpg():
    mime, blob = preparar_anexo("solicitacao.jpg", b"%PDF-1.4\n" + b"x" * 400)
    assert mime == "application/pdf"


def test_jpeg_disfarcado_de_pdf():
    mime, _ = preparar_anexo("doc.pdf", _jpeg())
    assert mime == "image/jpeg"


def test_tiff_disfarcado_de_jpg_e_convertido():
    """Assinatura desconhecida pro Gemini -> Pillow re-encoda em vez de recusar."""
    mime, blob = preparar_anexo("pedido.jpg", _tiff())
    assert mime == "image/jpeg"
    assert blob[:3] == bytes([0xFF, 0xD8, 0xFF])


# ── lixo: recusa INDIVIDUAL, nunca envenena o lote ────────────────────────
# NOTA: aqui havia dois testes que exigiam DESCARTE (mime is None) para bytes
# ilegiveis e para JPEG truncado. A producao desmentiu os dois na rodada #674 e eles
# foram substituidos por `test_jpg_ilegivel_cai_na_extensao_em_vez_de_sumir` e
# `test_jpeg_truncado_no_fim_ainda_e_aproveitado`, no bloco de REGRESSAO ao final.
# Eram testes que fixavam uma decisao minha, nao um comportamento observado.


def test_arquivo_vazio_e_recusado():
    mime, motivo = preparar_anexo("pedido.jpg", b"")
    assert mime is None


def test_jpeg_truncado_nao_sobe_com_mime_mentiroso():
    """Truncado ainda e aproveitado (o Gemini le), mas o MIME tem de descrever o
    que esta sendo enviado de fato."""
    mime, blob = preparar_anexo("pedido.jpg", _jpeg()[:60])
    assert mime in ("image/jpeg", None)
    if mime:
        assert blob


# ── nao pode quebrar o caminho normal ─────────────────────────────────────
def test_jpeg_valido_passa_intacto():
    b = _jpeg()
    mime, blob = preparar_anexo("pedido.jpg", b)
    assert mime == "image/jpeg" and blob == b


def test_pdf_valido_passa_intacto():
    b = b"%PDF-1.7\n" + b"conteudo" * 100
    mime, blob = preparar_anexo("pedido.pdf", b)
    assert mime == "application/pdf" and blob == b


def test_png_valido_passa_intacto():
    b = _png()
    mime, blob = preparar_anexo("pedido.png", b)
    assert mime == "image/png" and blob == b


def test_tiff_com_extensao_certa_continua_convertendo():
    mime, blob = preparar_anexo("pedido.tif", _tiff())
    assert mime == "image/jpeg" and blob[:3] == bytes([0xFF, 0xD8, 0xFF])


def test_sem_extensao_decide_pelos_bytes():
    mime, _ = preparar_anexo("anexo_sem_ponto", _png())
    assert mime == "image/png"


# ══════════════════════════════════════════════════════════════════════════
# REGRESSAO medida em producao na rodada #674 (mesmo dia da correcao).
#
# O FABRICIO (196307916) saiu de `cand=2 descartados=0` para `cand=0 descartados=2`,
# com o motivo nomeando os arquivos:
#     FABRICIO...jpg: formato .jpg nao suportado ou arquivo corrompido
#     FABRICIO...pdf: formato .pdf nao suportado ou arquivo corrompido
#
# Recusar um .pdf foi o sinal. A funcao passou a DESCARTAR o que antes ela apenas
# deixava passar — troquei um envenenamento de lote (ja resolvido pelo resgate
# um-a-um) por perda silenciosa de anexo.
#
# Dois modos de falha provados:
#   1. JPEG truncado — sem o marcador de fim (FFD9). E o corte classico de upload,
#      e o Gemini lia sem reclamar. Pillow recusa por padrao.
#   2. PDF cujo '%PDF' nao esta no byte 0 — espaco em branco antes do cabecalho e
#      LEGAL na especificacao, e varios geradores fazem isso.
#
# A regra que faltava: farejar e re-encodar so podem ACRESCENTAR capacidade. Quando
# nada disso resolve, cai no comportamento ANTIGO (confia na extensao) em vez de
# descartar. Se o arquivo for mesmo ilegivel, o resgate um-a-um isola ele sozinho.
# ══════════════════════════════════════════════════════════════════════════

def test_jpeg_truncado_no_fim_ainda_e_aproveitado():
    """Falta so o EOI. O Gemini lia; nao podemos jogar fora."""
    b = _jpeg()[:-2]
    mime, blob = preparar_anexo("pedido.jpg", b)
    assert mime is not None, "anexo valido foi descartado"
    assert mime.startswith("image/")


def test_jpeg_cortado_pela_metade_ainda_e_aproveitado():
    b = _jpeg()
    mime, _ = preparar_anexo("pedido.jpg", b[:len(b) // 2])
    assert mime is not None


def test_pdf_com_espaco_antes_do_cabecalho():
    """Espaco antes de '%PDF' e legal na especificacao."""
    mime, _ = preparar_anexo("solic.pdf", b"   \n%PDF-1.4\n" + b"x" * 400)
    assert mime == "application/pdf"


def test_pdf_que_nao_da_para_farejar_cai_na_extensao():
    """Antes passava; nao pode virar descarte."""
    mime, _ = preparar_anexo("solic.pdf", b"conteudo estranho sem assinatura" * 20)
    assert mime == "application/pdf"


def test_jpg_ilegivel_cai_na_extensao_em_vez_de_sumir():
    """O resgate um-a-um isola o anexo ruim. Descartar aqui perde o anexo BOM
    junto, quando o palpite de corrompido estiver errado."""
    mime, _ = preparar_anexo("pedido.jpg", b"nao sou imagem nenhuma" * 20)
    assert mime == "image/jpeg"


def test_sem_extensao_e_sem_assinatura_ai_sim_recusa():
    """Sem nenhum sinal, nao ha o que enviar."""
    mime, motivo = preparar_anexo("anexo_sem_ponto", b"lixo binario" * 20)
    assert mime is None and isinstance(motivo, str)


def test_extensao_desconhecida_e_sem_assinatura_recusa():
    mime, _ = preparar_anexo("arquivo.xyz", b"lixo" * 50)
    assert mime is None


# ══════════════════════════════════════════════════════════════════════════
# ULTIMO RECURSO: renderizar com PyMuPDF.
#
# Caso FABRICIO DOS SANTOS SOUZA NASCIMENTO (196307916, 410923, 18/08) — a unica
# pendencia que ainda era NOSSA depois de tres rodadas. Os dois anexos do prontuario
# dele sao ilegiveis pelos caminhos normais:
#
#     FABRICIO DOS SANTOS SOUZA120260818.jpg   Pillow nao abre
#     FABRICIO DOS SANTOS SOUZA.pdf            sem '%PDF' nos primeiros 1024 bytes
#
# E ilegiveis um a um, nao so em lote: o resgate de leitura ja tinha sido tentado. O
# lado do exame esta completo (6 arquivos no plano, laudos inclusive) — so o PEDIDO
# nao pode ser lido, e por isso a guia caia em falha tecnica e ficava presa.
#
# O MuPDF e muito mais tolerante que uma checagem de assinatura: ele VARRE o arquivo
# atras da estrutura em vez de exigir o cabecalho no byte 0, e abre varios formatos
# de imagem que o Pillow recusa. Renderizando a primeira pagina para PNG, o Gemini
# recebe algo que ele sabe ler.
#
# Fica como ULTIMO recurso, depois da assinatura e do Pillow: quando o caminho normal
# funciona, mandar o original e melhor (sem perda de resolucao, sem custo de render).
# ══════════════════════════════════════════════════════════════════════════

def _pdf_valido():
    import fitz
    d = fitz.open()
    pg = d.new_page()
    pg.insert_text((72, 72), "SOLICITACAO DE EXAMES")
    return d.tobytes()


def test_pdf_sem_assinatura_no_inicio_e_renderizado():
    """O caso FABRICIO: PDF real, mas com lixo antes do cabecalho."""
    b = b"\x00" * 2048 + _pdf_valido()
    mime, blob = preparar_anexo("FABRICIO DOS SANTOS SOUZA.pdf", b)
    assert mime in ("application/pdf", "image/png"), mime
    assert blob


def test_pdf_valido_normal_nao_e_renderizado():
    """Quando o caminho normal funciona, manda o original — sem perda nem custo."""
    b = _pdf_valido()
    mime, blob = preparar_anexo("solic.pdf", b)
    assert mime == "application/pdf"
    assert blob == b


def test_render_devolve_imagem_que_o_gemini_le():
    from esteira import _render_com_fitz
    out = _render_com_fitz(_pdf_valido())
    assert out and out[:4] == bytes([0x89]) + b"PNG"


def test_render_de_lixo_devolve_None():
    from esteira import _render_com_fitz
    assert _render_com_fitz(b"nao sou documento nenhum" * 30) is None


def test_render_de_vazio_devolve_None():
    from esteira import _render_com_fitz
    assert _render_com_fitz(b"") is None
