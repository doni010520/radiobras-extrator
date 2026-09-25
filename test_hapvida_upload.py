"""Upload Hapvida com HTTP falso — nenhum envio real."""
import pytest
from PIL import Image

import hapvida_upload as hu

FORM_HTML = """
<form name="form_img" method="post" enctype="multipart/form-data" action="https://exemplo/webhap/diretorio/insert_img_audi_odon.php">
<input type="file" name="dados" id="dados" required>
<input type="hidden" name="diretorio" id="diretorio" value="/auditoria_odonto/imagens">
<input type="hidden" name="nome_arq" id="nome_arq" value="">
</form>
<form name="form_proced" method="post" action="WebDentalAtendimento.pr_status_upload">
<input type="hidden" name="pguia" value="900002" id="pguia">
<input type="hidden" name="id_lobstore" value="555" >
<input type="hidden" name="pnm_arquivo" id="pnm_arquivo">
<input type="checkbox" id="item_1" value="1" name="pnu_item" >
<input type="checkbox" id="item_2" value="2" name="pnu_item" >
</form>
<script>formData.append("v_url", "http://storage/upload"); formData.append("hash", "abc");</script>
"""


class Resp:
    def __init__(self, body, status=200):
        self.content = body.encode(); self.status_code = status; self.ok = status < 400


class HttpFake:
    def __init__(self, blob_ok=True, lob_ok=True):
        self.posts = []; self.blob_ok = blob_ok; self.lob_ok = lob_ok
    def post(self, url, data=None, files=None, timeout=None):
        self.posts.append((url, data, files))
        if url.endswith("insert_img_audi_odon.php"):
            return Resp('{"success": %s}' % ("true" if self.blob_ok else "false"))
        if url.endswith("insert_img_aud_od_lob.php"):
            return Resp('{"ret": %d}' % (1 if self.lob_ok else 0))
        return Resp("<html>ok</html>")


class Sessao:
    cd_pessoa = "1"
    def __init__(self, http): self.http = http


class PortalFake:
    def __init__(self, http): self.s = Sessao(http)
    def _get(self, proc, **kw): return FORM_HTML


def _jpg(tmp_path, nome="ENTREGA_1.jpg"):
    p = tmp_path / nome; Image.new("RGB", (50, 50)).save(p, "JPEG"); return str(p)


def test_bloqueado_sem_variavel(tmp_path, monkeypatch):
    monkeypatch.delenv("HAPVIDA_ANEXAR_REAL", raising=False)
    http = HttpFake()
    with pytest.raises(hu.UploadBloqueado):
        hu.anexar(PortalFake(http), "X1", "900002", [_jpg(tmp_path)], 2)
    assert http.posts == []


def test_sequencia_blob_depois_status_com_todos_os_itens(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPVIDA_ANEXAR_REAL", "1")
    http = HttpFake()
    nomes = hu.anexar(PortalFake(http), "X1", "900002", [_jpg(tmp_path)], 2)
    assert nomes == ["X1-555.jpg"]
    (u1, d1, f1), (u2, d2, _) = http.posts
    assert u1.endswith("insert_img_audi_odon.php") and d1["nome_arq"] == "X1-555.jpg"
    assert d1["v_url"] == "http://storage/upload" and "dados" in f1
    assert u2.endswith("WebDentalAtendimento.pr_status_upload")
    assert ("pnu_item", "1") in d2 and ("pnu_item", "2") in d2 and ("pnm_arquivo", "X1-555.jpg") in d2


def test_cai_no_lob_quando_blob_recusa(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPVIDA_ANEXAR_REAL", "1")
    http = HttpFake(blob_ok=False)
    hu.anexar(PortalFake(http), "X1", "900002", [_jpg(tmp_path)], 2)
    assert [p[0].rsplit("/", 1)[-1] for p in http.posts] == [
        "insert_img_audi_odon.php", "insert_img_aud_od_lob.php", "WebDentalAtendimento.pr_status_upload"]


def test_storage_recusando_nao_marca_itens(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPVIDA_ANEXAR_REAL", "1")
    http = HttpFake(blob_ok=False, lob_ok=False)
    with pytest.raises(hu.UploadFalhou):
        hu.anexar(PortalFake(http), "X1", "900002", [_jpg(tmp_path)], 2)
    assert not any(p[0].endswith("pr_status_upload") for p in http.posts)


def test_guia_errada_no_formulario_aborta(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPVIDA_ANEXAR_REAL", "1")
    http = HttpFake()
    with pytest.raises(hu.UploadFalhou):
        hu.anexar(PortalFake(http), "X1", "999999", [_jpg(tmp_path)], 2)
    assert http.posts == []


def test_pdf_nunca_sobe(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPVIDA_ANEXAR_REAL", "1")
    p = tmp_path / "laudo.pdf"; p.write_bytes(b"%PDF")
    http = HttpFake()
    with pytest.raises(hu.UploadFalhou):
        hu.anexar(PortalFake(http), "X1", "900002", [str(p)], 2)
    assert http.posts == []
