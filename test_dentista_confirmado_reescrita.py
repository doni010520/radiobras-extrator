"""Reescrever a data de pedido vencido exige o dentista CONFIRMADO (10/10).

Caso RAFAELA (197457562): carimbo nao lido, a trava de 'outro dentista' falhou
aberta (ilegivel nao contradiz) e um pedido de maio, de outro dentista, saiu com a
data reescrita. Agora: CRO ou 2+ nomes batendo; senao rele so o carimbo; sem
confirmacao, nao reescreve."""
import json

import esteira

GTO = "17 - Nome do Profissional Solicitante ROSEANA FORTES 19 - Numero no Conselho 9752"
CANDS = [("pedido.jpg", "image/jpeg", b"img", None)]


class _Gem:
    def __init__(self, resp):
        self.n = 0
        gem = self

        class _M:
            def generate_content(self, **k):
                gem.n += 1
                return type("R", (), {"text": json.dumps(resp), "usage_metadata": None})()
        self.models = _M()


def _a(dentista="", cro=""):
    return {"idx": 0, "tipo": "solicitacao", "dentista_lido": dentista, "cro_lido": cro}


def test_cro_bate_confirma_sem_releitura():
    g = _Gem({})
    assert esteira._dentista_confirmado(g, CANDS, _a(cro="9752"), "ROSEANA FORTES", GTO)
    assert g.n == 0


def test_nome_completo_bate_confirma():
    assert esteira._dentista_confirmado(None, CANDS, _a("Roseana Fortes"), "ROSEANA FORTES", GTO)


def test_carimbo_ilegivel_rele_e_confirma_pelo_cro():
    g = _Gem({"cro": "9752", "dentista": "Roseana Fortes"})
    a = _a("")
    assert esteira._dentista_confirmado(g, CANDS, a, "ROSEANA FORTES", GTO)
    assert g.n == 1 and a["cro_lido"] == "9752"


def test_carimbo_ilegivel_que_continua_ilegivel_nao_confirma():
    """O caso RAFAELA: antes passava (ilegivel nao contradiz)."""
    g = _Gem({"cro": "", "dentista": ""})
    assert not esteira._dentista_confirmado(g, CANDS, _a(""), "ROSEANA FORTES", GTO)


def test_outro_dentista_nao_confirma():
    g = _Gem({"cro": "1111", "dentista": "Outra Pessoa"})
    assert not esteira._dentista_confirmado(g, CANDS, _a("Outra Pessoa"), "ROSEANA FORTES", GTO)


def test_guia_sem_referencia_nao_bloqueia():
    assert esteira._dentista_confirmado(None, CANDS, _a(""), "", "")


def test_mensagem_cai_no_grupo_proprio():
    import db
    m = ("NÃO FATUROU porque o pedido encontrado tem data vencida (01/05/2026) e o "
         "carimbo não confirma o dentista da guia (ROSEANA FORTES): lido 'ilegível'.")
    assert db.classificar_pendencia(m, "sem_solicitacao")[0] == "dentista_nao_confirmado"
