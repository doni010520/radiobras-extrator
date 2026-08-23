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
def test_bytes_ilegiveis_sao_recusados_com_motivo():
    mime, motivo = preparar_anexo("pedido.jpg", b"nao sou imagem nenhuma" * 20)
    assert mime is None
    assert isinstance(motivo, str) and motivo


def test_arquivo_vazio_e_recusado():
    mime, motivo = preparar_anexo("pedido.jpg", b"")
    assert mime is None


def test_jpeg_truncado_e_recusado_e_nao_sobe_como_jpeg():
    """O modo de falha real: upload cortado pela metade. A assinatura BATE (comeca
    com FFD8FF), entao farejar nao basta — tem de conseguir DECODIFICAR."""
    mime, _ = preparar_anexo("pedido.jpg", _jpeg()[:60])
    assert mime is None


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
