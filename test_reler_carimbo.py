"""Carimbo do dentista lido errado virava "pedido de OUTRO dentista" (frente A, 08/10).

Seis guias recusadas com o carimbo CERTO no papel: o Gemini trocava o carimbo pelo
timbre impresso da clinica, ou lia errado o carimbo girado 180 graus. Ex.: GABRIEL
197184354 (Roseana Fortes, CRO 9752, carimbo de cabeca para baixo) lido "Roberta
Lopes 9760"; JOYCE 197529751 (CRO 13520) lido 13320; LAMEIDE/ELIMARCIA (Joice Nunes,
CRO 26995) lidos "Iolanda 10464" / "Girlene 5995".

Antes de recusar por OUTRO_DENTISTA: reler SO o carimbo, sem dizer o nome esperado.
Aceita apenas se o CRO relido aparecer EXATAMENTE no texto da guia. Nada afrouxa:
sem CRO batendo, continua recusado (a trava que barrou a INGRID segue valendo)."""
import json

import esteira

GTO_TXT = "17 - Nome do Profissional Solicitante ROSEANA FORTES 19 - Numero no Conselho 9752"


class _Gem:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.enviados = []
        gem = self

        class _M:
            def generate_content(self, model=None, contents=None, config=None):
                gem.enviados.append(contents)
                return type("R", (), {"text": json.dumps(gem.respostas.pop(0))})()
        self.models = _M()


def _leitura():
    return {"idx": 0, "tipo": "solicitacao", "legivel": True,
            "paciente_lido": "GABRIEL SILVA", "dentista_lido": "Roberta Lopes",
            "cro_lido": "9760", "exames_lidos": ["panoramica"]}


CANDS = [("pedido.jpg", "image/jpeg", b"\xff\xd8img", None)]


def test_cro_relido_que_bate_com_a_guia_libera():
    lei = [_leitura()]
    assert esteira._dentista_contradiz(lei[0], "ROSEANA FORTES", GTO_TXT)
    n = esteira._reler_carimbo(_Gem([{"cro": "9752", "dentista": "Roseana Fortes"}]),
                              CANDS, lei, "ROSEANA FORTES", GTO_TXT)
    assert n == 1
    assert lei[0]["cro_lido"] == "9752" and lei[0]["cro_lido_1a"] == "9760"
    assert not esteira._dentista_contradiz(lei[0], "ROSEANA FORTES", GTO_TXT)


def test_cro_relido_que_nao_bate_nao_muda_nada():
    lei = [_leitura()]
    n = esteira._reler_carimbo(_Gem([{"cro": "1234", "dentista": "Outro Nome"}]),
                              CANDS, lei, "ROSEANA FORTES", GTO_TXT)
    assert n == 0 and lei[0]["cro_lido"] == "9760"
    assert esteira._dentista_contradiz(lei[0], "ROSEANA FORTES", GTO_TXT)


def test_cro_curto_nao_vale_como_prova():
    lei = [_leitura()]
    n = esteira._reler_carimbo(_Gem([{"cro": "52", "dentista": ""}]),
                              CANDS, lei, "ROSEANA FORTES", GTO_TXT)
    assert n == 0


def test_so_rele_quem_esta_barrado_pelo_dentista():
    ok = _leitura()
    ok["cro_lido"] = "9752"                     # ja confere: nao gasta releitura
    gem = _Gem([])
    assert esteira._reler_carimbo(gem, CANDS, [ok], "ROSEANA FORTES", GTO_TXT) == 0
    assert gem.enviados == []


def test_o_nome_esperado_nao_vai_no_pedido_ao_gemini():
    """Dizer o nome esperado induz a 'ver' o que se sugere (mesmo cuidado da
    releitura de exames)."""
    gem = _Gem([{"cro": "9752", "dentista": "x"}])
    esteira._reler_carimbo(gem, CANDS, [_leitura()], "ROSEANA FORTES", GTO_TXT)
    texto = " ".join(str(p) for p in gem.enviados[0] if isinstance(p, str))
    assert "ROSEANA" not in texto.upper() and "9752" not in texto


def test_erro_do_gemini_nao_derruba_e_nao_libera():
    class _GemErro:
        class models:
            @staticmethod
            def generate_content(**k):
                raise RuntimeError("503")
    lei = [_leitura()]
    assert esteira._reler_carimbo(_GemErro(), CANDS, lei, "ROSEANA FORTES", GTO_TXT) == 0
    assert lei[0]["cro_lido"] == "9760"
