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
