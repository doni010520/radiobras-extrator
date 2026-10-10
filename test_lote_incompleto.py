"""Lote que responde, mas sem a leitura de algum anexo (10/10).

O Gemini le todos os anexos numa chamada e devolve uma leitura por anexo. As vezes
devolve MENOS: o pedido do dentista simplesmente nao aparece na resposta. O codigo
decidia so com o que veio e a guia virava "sem pedido" com o pedido no prontuario
(THALLES 198020117, LEANDRO 197568862). Agora: anexo sem leitura e relido sozinho,
e a decisao sai com todos."""
import json

import pytest

import esteira


class _Resp:
    def __init__(self, data):
        self.text = json.dumps(data)
        self.usage_metadata = None


class _Modelos:
    def __init__(self, lote):
        self.lote = lote
        self.individuais = []

    def generate_content(self, model=None, contents=None, config=None):
        if len(contents or []) > 3:
            return _Resp(self.lote)
        self.individuais.append(contents[1])
        return _Resp({"anexos": [{"idx": 0, "tipo": "solicitacao", "lido": contents[1].decode()}]})


class _Gem:
    def __init__(self, lote):
        self.models = _Modelos(lote)


def _cands(n):
    return [(f"a{i}.jpg", "image/jpeg", f"blob{i}".encode(), None) for i in range(n)]


def _contents(n):
    return [x for i in range(n) for x in (f"[anexo {i}]", b"x")] + ["PROMPT"]


@pytest.fixture(autouse=True)
def _limpa(monkeypatch):
    esteira._gem_estado["fatal"] = None
    from google.genai import types
    monkeypatch.setattr(types.Part, "from_bytes",
                        staticmethod(lambda data=None, mime_type=None: data))
    yield
    esteira._gem_estado["fatal"] = None


def _leituras(data):
    return (data.get("anexos") if isinstance(data, dict) else data) or []


def test_anexo_que_faltou_no_lote_e_relido_sozinho():
    gem = _Gem({"anexos": [{"idx": 0, "tipo": "gto"}, {"idx": 2, "tipo": "laudo"}]})
    data, _ = esteira._ler_lote_com_resgate(gem, _cands(3), _contents(3))
    ls = _leituras(data)
    assert sorted(a["idx"] for a in ls) == [0, 1, 2]
    assert gem.models.individuais == [b"blob1"]          # so o que faltou
    relido = next(a for a in ls if a["idx"] == 1)
    assert relido["tipo"] == "solicitacao" and relido["lido"] == "blob1"


def test_lote_completo_nao_gasta_chamada_extra():
    gem = _Gem({"anexos": [{"idx": 0}, {"idx": 1}]})
    esteira._ler_lote_com_resgate(gem, _cands(2), _contents(2))
    assert gem.models.individuais == []


def test_idx_como_texto_conta_como_lido():
    gem = _Gem({"anexos": [{"idx": "0"}, {"idx": "1"}]})
    esteira._ler_lote_com_resgate(gem, _cands(2), _contents(2))
    assert gem.models.individuais == []


def test_lote_em_lista_tambem_e_completado():
    gem = _Gem([{"idx": 1, "tipo": "gto"}])
    data, _ = esteira._ler_lote_com_resgate(gem, _cands(2), _contents(2))
    assert sorted(a["idx"] for a in _leituras(data)) == [0, 1]


def test_releitura_que_falha_nao_derruba_o_que_ja_veio():
    class _M(_Modelos):
        def generate_content(self, model=None, contents=None, config=None):
            if len(contents or []) > 3:
                return _Resp(self.lote)
            raise RuntimeError("503")
    gem = _Gem({"anexos": [{"idx": 0, "tipo": "gto"}]})
    gem.models = _M({"anexos": [{"idx": 0, "tipo": "gto"}]})
    data, _ = esteira._ler_lote_com_resgate(gem, _cands(2), _contents(2))
    assert [a["idx"] for a in _leituras(data)] == [0]
