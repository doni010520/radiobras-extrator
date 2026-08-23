"""Um anexo ruim nao pode envenenar a leitura da guia inteira.

Caso FABRICIO DOS SANTOS SOUZA NASCIMENTO (196307916) e as duas DILMA DA CONCEICAO
(196307961, 196308165) — 18/08, tres das quatro pendencias tecnicas abertas em 23/08,
todas a 2 dias do prazo.

A leitura manda TODOS os anexos do prontuario numa chamada so. Quando um deles e
malformado, a API rejeita o lote inteiro (400/INVALID_ARGUMENT) — a chamada LEVANTA
excecao. O resgate `_ler_anexos_um_a_um` existe desde 31/07 (caso SOPHIA) e resolve
exatamente isso, mas estava pendurado no `except` do `json.loads`:

    try:
        r = gem.models.generate_content(...)   # <- excecao aqui pula tudo
        ...
        try:
            data = json.loads(txt)
        except Exception:
            data = _ler_anexos_um_a_um(gem, cands)   # <- so alcanca JSON degenerado

Ou seja: o resgate cobria o lote que respondia MAL e nao o lote que nao respondia. O
`except` de fora apenas re-tentava o MESMO lote tres vezes, com o mesmo anexo ruim
dentro, e a guia terminava em falha tecnica — repetindo ate esgotar o retry.

Lendo um a um, o anexo ruim tira so a si mesmo da jogada e a guia segue com o resto.
Erro FATAL (cota/credito/chave) continua parando na hora: ali re-tentar de outro
jeito so queima tempo."""
import json

import pytest


def _anexos(data):
    """O resgate devolve LISTA, o lote devolve dict {"anexos": [...]}. O chamador
    aceita as duas formas — os testes tambem."""
    return (data.get("anexos") if isinstance(data, dict) else data) or []

import esteira


class _Resp:
    def __init__(self, texto):
        self.text = texto
        self.usage_metadata = None


class _Modelos:
    """Gemini falso: o LOTE quebra, a leitura individual funciona."""
    def __init__(self, erro_do_lote):
        self.erro_do_lote = erro_do_lote
        self.chamadas_lote = 0
        self.chamadas_uma = 0

    def generate_content(self, model=None, contents=None, config=None):
        marcadores = sum(1 for c in (contents or []) if c == "[anexo 0]")
        if len(contents or []) > 3:                      # lote: varios anexos
            self.chamadas_lote += 1
            raise self.erro_do_lote
        self.chamadas_uma += 1                           # resgate: um anexo
        assert marcadores == 1
        return _Resp(json.dumps({"anexos": [
            {"idx": 0, "tipo": "solicitacao", "legivel": True,
             "paciente_lido": "FABRICIO DOS SANTOS SOUZA NASCIMENTO"}]}))


class _Gem:
    def __init__(self, erro_do_lote):
        self.models = _Modelos(erro_do_lote)


def _cands(n=3):
    return [(f"a{i}.pdf", "application/pdf", b"%PDF-1.4", None) for i in range(n)]


def _contents(n=3):
    return ([x for i in range(n) for x in (f"[anexo {i}]", b"blob")] + ["PROMPT"])


@pytest.fixture(autouse=True)
def _limpa_estado():
    esteira._gem_estado["fatal"] = None
    yield
    esteira._gem_estado["fatal"] = None


# ── o caso ────────────────────────────────────────────────────────────────
def test_lote_que_levanta_excecao_cai_no_resgate():
    gem = _Gem(RuntimeError("400 INVALID_ARGUMENT: unable to process input image"))
    data, um_a_um = esteira._ler_lote_com_resgate(gem, _cands(3), _contents(3))
    assert um_a_um is True
    assert _anexos(data), "a guia tinha de sair com leitura, nao com falha tecnica"
    assert gem.models.chamadas_uma == 3


def test_nao_re_tenta_o_mesmo_lote_envenenado():
    """Tres tentativas do mesmo lote com o mesmo anexo ruim dentro sao tres
    fracassos identicos — era o que acontecia."""
    gem = _Gem(RuntimeError("400 INVALID_ARGUMENT"))
    esteira._ler_lote_com_resgate(gem, _cands(2), _contents(2))
    assert gem.models.chamadas_lote == 1


# ── o resgate que ja existia continua valendo ─────────────────────────────
def test_json_degenerado_continua_caindo_no_resgate():
    """Caso SOPHIA (31/07): o lote responde, mas com JSON truncado."""
    class _M(_Modelos):
        def generate_content(self, model=None, contents=None, config=None):
            if len(contents or []) > 3:
                self.chamadas_lote += 1
                return _Resp('{"anexos": [{"idx": 0, "tipo": "solici')   # truncado
            return _Modelos.generate_content(self, model, contents, config)
    gem = _Gem(RuntimeError("x")); gem.models = _M(RuntimeError("x"))
    data, um_a_um = esteira._ler_lote_com_resgate(gem, _cands(2), _contents(2))
    assert um_a_um is True and _anexos(data)


def test_lote_bom_nao_aciona_resgate():
    """Caminho normal: uma chamada, nenhuma leitura individual."""
    class _M(_Modelos):
        def generate_content(self, model=None, contents=None, config=None):
            self.chamadas_lote += 1
            return _Resp(json.dumps({"anexos": [{"idx": 0, "tipo": "solicitacao"}]}))
    gem = _Gem(RuntimeError("x")); gem.models = _M(RuntimeError("x"))
    data, um_a_um = esteira._ler_lote_com_resgate(gem, _cands(2), _contents(2))
    assert um_a_um is False
    assert gem.models.chamadas_uma == 0


# ── erro FATAL nao vira resgate ───────────────────────────────────────────
def test_cota_estourada_falha_na_hora():
    """Sem credito, ler um a um so gasta tempo e multiplica chamadas mortas."""
    gem = _Gem(RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"))
    with pytest.raises(Exception):
        esteira._ler_lote_com_resgate(gem, _cands(3), _contents(3))
    assert gem.models.chamadas_uma == 0


def test_resgate_que_tambem_falha_propaga_o_erro_do_lote():
    """Se nem um a um le nada, a guia tem de terminar com o erro REAL do lote —
    nao com um erro generico que esconde a causa."""
    class _M(_Modelos):
        def generate_content(self, model=None, contents=None, config=None):
            raise RuntimeError("400 INVALID_ARGUMENT")
    gem = _Gem(RuntimeError("400 INVALID_ARGUMENT")); gem.models = _M(RuntimeError("x"))
    with pytest.raises(Exception) as ei:
        esteira._ler_lote_com_resgate(gem, _cands(2), _contents(2))
    assert "INVALID_ARGUMENT" in str(ei.value)
