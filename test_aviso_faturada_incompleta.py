"""Aviso de guia FATURADA INCOMPLETA — a conferencia pos-rodada.

Ate 01/09 o sistema confiava no proprio relato: se o upload disse OK, a guia
constava faturada. Ninguem voltava ao portal para perguntar se ela ficou completa.
Foi essa diferenca entre "o robo diz que anexou" e "o convenio confirma que esta
la" que produziu as 54 guias vencidas descobertas em 29/08.

Este aviso e o contrario de barulho: em condicao normal ele nunca dispara. Na
primeira conferencia real (01/09, 16 guias) o resultado foi 16 completas, zero
incompletas. Se ele tocar, e porque algo passou pelo portao e nao deveria."""
import pytest

import notificador


@pytest.fixture(autouse=True)
def _canal_ligado(monkeypatch):
    """O canal so envia com a uazapi configurada — sem isso o teste passaria por
    engano (nao enviou porque nao ha canal, nao porque nao havia o que avisar)."""
    monkeypatch.setenv("UAZAPI_HOST", "https://benitechlab.uazapi.com")
    monkeypatch.setenv("UAZAPI_TOKEN", "token-de-teste")
    monkeypatch.setenv("ALERTA_WHATSAPP_TO", "557193061031")
    monkeypatch.delenv("ALERTA_FALHA", raising=False)


class _Espia:
    def __init__(self): self.textos = []
    def __call__(self, url, headers, payload):
        self.textos.append(payload.get("text", ""))
        return True


def _item(gto, pac, dia, conta, falta):
    return {"gto": gto, "paciente": pac, "dia": dia, "conta": conta, "falta": falta}


def test_nao_manda_nada_quando_esta_tudo_completo():
    espia = _Espia()
    assert notificador.avisar_faturada_incompleta([], _post=espia) is False
    assert espia.textos == [], "mandou mensagem sem ter o que avisar"


def test_avisa_com_o_paciente_a_guia_e_o_que_falta():
    espia = _Espia()
    itens = [_item("196718380", "MARCIO DAVID PALMEIRA", "27/08/2026", "388336", "imagem")]
    assert notificador.avisar_faturada_incompleta(itens, _post=espia) is True
    txt = espia.textos[0]
    assert "196718380" in txt
    assert "MARCIO DAVID PALMEIRA" in txt
    assert "27/08" in txt
    assert "imagem" in txt.lower()


def test_uma_mensagem_so_para_varias_guias():
    """Uma linha por guia, uma mensagem por rodada — nao um WhatsApp por guia."""
    espia = _Espia()
    itens = [_item("1", "A", "27/08/2026", "388336", "laudo"),
             _item("2", "B", "27/08/2026", "397950", "imagem"),
             _item("3", "C", "28/08/2026", "410923", "laudo")]
    notificador.avisar_faturada_incompleta(itens, _post=espia)
    assert len(espia.textos) == 1
    txt = espia.textos[0]
    assert txt.count("196") == 0 and "A" in txt and "B" in txt and "C" in txt


def test_diz_que_a_guia_JA_FOI_faturada():
    """A acao aqui e diferente de uma pendencia: a guia ja foi enviada ao convenio,
    entao o caminho e completar antes do prazo, nao esperar o robo."""
    espia = _Espia()
    notificador.avisar_faturada_incompleta(
        [_item("9", "X", "27/08/2026", "388336", "imagem")], _post=espia)
    assert "faturada" in espia.textos[0].lower()
