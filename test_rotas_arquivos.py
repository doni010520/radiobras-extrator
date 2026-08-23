"""Rotas dos arquivos da pendencia: as travas que protegem a operadora.

Os arquivos vem do prontuario do PRORADIS — ou seja, de TERCEIRO. Duas coisas nao
podem acontecer, e sao as duas que estes testes travam:

1. ESCAPAR DA PASTA. O nome do arquivo chega pela URL. Sem trava daria para pedir
   `../../.env` e baixar as credenciais do sistema (DATABASE_URL, GEMINI_API_KEY,
   senha do OdontoPrev).
2. XSS COM O COOKIE DELA. Um anexo `.html`/`.svg` servido inline renderiza como
   pagina dentro da sessao autenticada da operadora.

Levantado na verificacao adversarial de 22/08."""
import os

import pytest


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "teste")
    monkeypatch.setenv("PENDENCIAS_DIR", str(tmp_path / "dados"))
    import app as appmod
    import arquivos_pendencia as ap
    import db

    # pendencia de mentira: a rota resolve conta/dia/gto/paciente pelo BANCO
    monkeypatch.setattr(db, "pendencia_por_id", lambda pid: {
        "id": pid, "conta": "388336", "dia": "14/08/2026", "gto": "196215069",
        "paciente": "JACIARA RIBEIRO SANTANA", "categoria": "sem_solicitacao",
        "resolvido": False})

    origem = tmp_path / "dl"
    origem.mkdir()
    (origem / "ENTREGA_a.jpg").write_bytes(b"\xff\xd8imagem")
    (origem / "LAUDO_X_1_OFICIAL.pdf").write_bytes(b"%PDF-1.4")
    (origem / "malicioso.html").write_bytes(b"<script>alert(1)</script>")
    ap.guardar(str(tmp_path / "dados"), "388336", "14/08/2026", "196215069",
               "JACIARA RIBEIRO SANTANA", str(origem),
               ["ENTREGA_a.jpg", "LAUDO_X_1_OFICIAL.pdf", "malicioso.html"])

    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    with c.session_transaction() as ses:      # passa pelo guard de login
        ses["uid"] = 1
        ses["username"] = "teste"
        ses["role"] = "admin"
    monkeypatch.setattr(appmod, "_usuario_valido",
                        lambda uid: {"id": uid, "role": "admin"})
    return c


def test_lista_os_arquivos(cliente):
    r = cliente.get("/pendencias/1/arquivos")
    assert r.status_code == 200
    nomes = sorted(i["nome"] for i in r.get_json()["itens"])
    assert nomes == ["ENTREGA_a.jpg", "LAUDO_X_1_OFICIAL.pdf", "malicioso.html"]


def test_baixa_arquivo_legitimo(cliente):
    r = cliente.get("/pendencias/1/arquivo/ENTREGA_a.jpg")
    assert r.status_code == 200
    assert r.data.startswith(b"\xff\xd8")


def test_nao_deixa_escapar_da_pasta(cliente):
    """Sem esta trava, `../../.env` entregaria DATABASE_URL, GEMINI_API_KEY e a
    senha do OdontoPrev para quem tiver qualquer login do app."""
    for mau in ("..%2F..%2F.env", "..%5C..%5C.env", "subdir%2Fa.pdf"):
        r = cliente.get("/pendencias/1/arquivo/" + mau)
        assert r.status_code == 404, mau


def test_html_do_prontuario_NUNCA_abre_inline(cliente):
    """O anexo veio de terceiro. Inline, um .html roda script na sessao dela."""
    r = cliente.get("/pendencias/1/arquivo/malicioso.html?inline=1")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_imagem_pode_abrir_inline(cliente):
    r = cliente.get("/pendencias/1/arquivo/ENTREGA_a.jpg?inline=1")
    assert "attachment" not in r.headers.get("Content-Disposition", "")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_zip_traz_tudo(cliente):
    import io
    import zipfile
    r = cliente.get("/pendencias/1/arquivos.zip")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        assert sorted(z.namelist()) == ["ENTREGA_a.jpg", "LAUDO_X_1_OFICIAL.pdf",
                                        "malicioso.html"]


def test_sem_login_nao_alcanca(tmp_path, monkeypatch):
    """As rotas herdam o guard de _exigir_login por estarem sob /pendencias — se
    alguem mudar o prefixo, este teste avisa antes de virar vazamento."""
    monkeypatch.setenv("SECRET_KEY", "teste")
    monkeypatch.setenv("PENDENCIAS_DIR", str(tmp_path))
    import app as appmod
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    r = c.get("/pendencias/1/arquivo/qualquer.jpg")
    assert r.status_code in (302, 401)     # redireciona pro login ou 401 (JSON)
