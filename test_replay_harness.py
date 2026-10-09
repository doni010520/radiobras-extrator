import pytest

import replay_harness as rh


def test_cassete_grava_e_toca_valor_e_blob(tmp_path):
    c = rh.Cassete(str(tmp_path / "c1"))
    sha = c.gravar_blob(b"PDF-bytes")
    c.gravar("baixa_um", "197100679", {"status": "BAIXADO", "blob": sha})
    c.meta["dia"] = "08/09/2026"
    c.salvar()
    c2 = rh.Cassete(str(tmp_path / "c1"))
    assert c2.tocar("baixa_um", "197100679")["status"] == "BAIXADO"
    assert c2.ler_blob(sha) == b"PDF-bytes"
    assert c2.meta["dia"] == "08/09/2026"


def test_tocar_chave_ausente_falha_alto(tmp_path):
    c = rh.Cassete(str(tmp_path / "c2"))
    with pytest.raises(rh.ReplayFaltando, match="baixa_um"):
        c.tocar("baixa_um", "999")


def test_chave_gemini_estavel_e_sensivel_ao_conteudo():
    class _Part:
        def __init__(self, text=None, data=None, mime=None):
            self.text = text
            self.inline_data = type("D", (), {"data": data, "mime_type": mime})() if data else None
    a = rh.chave_gemini("gemini-2.5-flash", ["leia", _Part(data=b"\x01\x02", mime="image/png")])
    b = rh.chave_gemini("gemini-2.5-flash", ["leia", _Part(data=b"\x01\x02", mime="image/png")])
    c = rh.chave_gemini("gemini-2.5-flash", ["leia", _Part(data=b"\x01\x03", mime="image/png")])
    assert a == b and a != c


def test_contexto_falso_entrega_bearer_para_quem_escuta_request():
    ctx = rh.ContextoFalso()
    vistos = []
    ctx.on("request", lambda req: vistos.append(req.headers.get("authorization")))
    assert vistos == ["Bearer REPLAY"]
    assert ctx.cookies() == [] and ctx.storage_state() == {}


def test_playwright_falso_nos_dois_jeitos_de_uso():
    with rh.PlaywrightFalso() as pw:
        br = pw.chromium.launch(headless=True)
        pg = br.new_context().new_page()
        pg.goto("x"); pg.wait_for_timeout(5)
    p2 = rh.PlaywrightFalso().start(); p2.stop()


def test_gemini_grava_e_toca(tmp_path):
    c = rh.Cassete(str(tmp_path / "g"))

    class _RealResp:
        text = '{"ok": true}'

    class _RealModels:
        def generate_content(self, model, contents, config=None):
            return _RealResp()

    class _RealClient:
        def __init__(self, api_key=None):
            self.models = _RealModels()

    Grav = rh.gemini_cliente("gravar", c, _RealClient)
    assert Grav(api_key="k").models.generate_content(model="m", contents=["oi"]).text == '{"ok": true}'
    Toca = rh.gemini_cliente("tocar", c, None)
    assert Toca(api_key="k").models.generate_content(model="m", contents=["oi"]).text == '{"ok": true}'
    with pytest.raises(rh.ReplayFaltando):
        Toca(api_key="k").models.generate_content(model="m", contents=["outro"])


def test_requests_grava_e_toca_bytes_e_status(tmp_path):
    c = rh.Cassete(str(tmp_path / "r"))

    class _R:
        status_code, text, content, headers = 200, '[{"a":1}]', b'[{"a":1}]', {"Content-Type": "application/json"}
        def json(self):
            return [{"a": 1}]

    class _RealSess:
        def __init__(self):
            self.headers, self.cookies = {}, type("J", (), {"update": lambda s, x: None})()
        def get(self, url, **kw):
            return _R()

    G = rh.sessao_classe("gravar", c, _RealSess)
    assert G().get("https://x/v1/gto/imagens?numeroFicha=1", timeout=20).json() == [{"a": 1}]
    T = rh.sessao_classe("tocar", c, None)
    r = T().get("https://x/v1/gto/imagens?numeroFicha=1", timeout=5)
    assert r.status_code == 200 and r.json() == [{"a": 1}] and r.content == b'[{"a":1}]'


import esteira


def test_instalar_troca_e_restaura_as_costuras(tmp_path):
    original = esteira._baixa_um
    c = rh.Cassete(str(tmp_path / "i"))
    c.gravar("baixa_um", "123", {"gto": "123", "status": "SEM_MATCH", "arquivos": []})
    with rh.instalar("tocar", c):
        assert esteira._baixa_um is not original
        r = esteira._baixa_um(None, None, {}, {"gto": "123"}, str(tmp_path), "01/10/2026")
        assert r["status"] == "SEM_MATCH"
    assert esteira._baixa_um is original


def test_baixa_um_recria_a_pasta_com_os_mesmos_bytes(tmp_path):
    c = rh.Cassete(str(tmp_path / "p"))
    sha = c.gravar_blob(b"LAUDO")
    c.gravar("baixa_um", "9", {"gto": "9", "status": "BAIXADO",
                               "_arquivos": {"LAUDO_PANORAMICA_1_OFICIAL.pdf": sha},
                               "_pasta": "X"})
    with rh.instalar("tocar", c):
        r = esteira._baixa_um(None, None, {}, {"gto": "9"}, str(tmp_path / "tmp"), "01/10/2026")
    import os
    assert open(os.path.join(r["_pasta"], "LAUDO_PANORAMICA_1_OFICIAL.pdf"), "rb").read() == b"LAUDO"


def test_relogio_congelado_no_instante_da_gravacao(tmp_path):
    c = rh.Cassete(str(tmp_path / "t"))
    c.meta["agora"] = "2026-10-09T10:00:00"
    with rh.instalar("tocar", c):
        assert esteira.datetime.now().isoformat().startswith("2026-10-09T10:00")


def test_veredito_normaliza_e_indexa_por_gto():
    resumo = {"decisoes": [
        {"gto": "2", "categoria": "auto", "anexado": "DRY",
         "arquivos_anexados": ["b.pdf", "a.jpg"], "gemini": {"motivo": "ok"}},
        {"gto": "1", "categoria": "sem_exame", "anexado": None, "gemini": {"motivo": "x"},
         "erro": None}]}
    v = rh.veredito(resumo)
    assert list(v) == ["1", "2"]
    assert v["2"] == {"categoria": "auto", "anexado": "DRY", "arquivos": ["a.jpg", "b.pdf"], "motivo": "ok"}


def test_gravador_gemini_mantem_o_cliente_real_vivo(tmp_path):
    """Se so .models fosse guardado, o cliente real seria coletado e fecharia."""
    import gc
    vivos = []

    class _RealModels:
        def __init__(self, dono):
            self.dono = dono
        def generate_content(self, model, contents, config=None):
            assert not self.dono.fechado, "client has been closed"
            return type("R", (), {"text": "ok"})()

    class _RealClient:
        def __init__(self, api_key=None):
            self.fechado = False
            self.models = _RealModels(self)
            vivos.append(self)
        def __del__(self):
            self.fechado = True

    vivos.clear()
    c = rh.Cassete(str(tmp_path / "gv"))
    cli = rh.gemini_cliente("gravar", c, _RealClient)(api_key="k")
    vivos.clear(); gc.collect()
    assert cli.models.generate_content(model="m", contents=["x"]).text == "ok"
