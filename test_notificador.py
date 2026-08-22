"""Aviso de FALHA TECNICA ao dono, por WhatsApp (uazapi).

Regra do dono (22/08/26): falha de sistema nao aparece no painel da RadioBras —
ela e notificada A MIM e re-tentada pelo proprio sistema. Este modulo e so o
canal; quem decide o que e falha nossa e `db.eh_nosso`.

Principio inegociavel: AVISO NUNCA DERRUBA FATURAMENTO. Qualquer erro de rede,
config faltando ou resposta estranha da uazapi retorna False e segue a vida — o
padrao do `_send_email` do app.py."""
import os
import pytest
import notificador


@pytest.fixture(autouse=True)
def _env_limpo(monkeypatch):
    for k in ("UAZAPI_HOST", "UAZAPI_TOKEN", "ALERTA_WHATSAPP_TO", "ALERTA_FALHA",
              "APP_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


def _configura(monkeypatch, **extra):
    monkeypatch.setenv("UAZAPI_HOST", "https://benitechlab.uazapi.com")
    monkeypatch.setenv("UAZAPI_TOKEN", "token-de-teste")
    monkeypatch.setenv("ALERTA_WHATSAPP_TO", "557193061031")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


class _Espia:
    """Substitui o POST de verdade e guarda o que teria sido enviado."""
    def __init__(self, ok=True, boom=None):
        self.ok, self.boom, self.chamadas = ok, boom, []

    def __call__(self, url, headers, payload):
        self.chamadas.append({"url": url, "headers": headers, "payload": payload})
        if self.boom:
            raise self.boom
        return self.ok


# ── configuracao ────────────────────────────────────────────────────────────
def test_sem_config_nao_envia_e_nao_levanta():
    # produção pode subir sem as envs; o robô não pode quebrar por causa disso
    assert notificador.whatsapp_configurado() is False
    assert notificador.enviar_whatsapp("oi") is False


def test_gate_desligado_nao_envia(monkeypatch):
    _configura(monkeypatch, ALERTA_FALHA="0")
    espia = _Espia()
    assert notificador.enviar_whatsapp("oi", _post=espia) is False
    assert espia.chamadas == []


# ── envio ───────────────────────────────────────────────────────────────────
def test_envia_no_formato_da_uazapi(monkeypatch):
    _configura(monkeypatch)
    espia = _Espia()
    assert notificador.enviar_whatsapp("teste", _post=espia) is True
    c = espia.chamadas[0]
    assert c["url"] == "https://benitechlab.uazapi.com/send/text"
    assert c["headers"]["token"] == "token-de-teste"
    assert c["payload"]["number"] == "557193061031"
    assert c["payload"]["text"] == "teste"


def test_host_sem_barra_final_nao_duplica(monkeypatch):
    _configura(monkeypatch, UAZAPI_HOST="https://benitechlab.uazapi.com/")
    espia = _Espia()
    notificador.enviar_whatsapp("x", _post=espia)
    assert espia.chamadas[0]["url"] == "https://benitechlab.uazapi.com/send/text"


def test_erro_de_rede_nunca_levanta(monkeypatch):
    _configura(monkeypatch)
    espia = _Espia(boom=RuntimeError("uazapi fora do ar"))
    assert notificador.enviar_whatsapp("x", _post=espia) is False   # nao levanta


def test_varios_destinos_separados_por_virgula(monkeypatch):
    _configura(monkeypatch, ALERTA_WHATSAPP_TO="557193061031, 5571999999999")
    espia = _Espia()
    assert notificador.enviar_whatsapp("x", _post=espia) is True
    assert [c["payload"]["number"] for c in espia.chamadas] == ["557193061031",
                                                                "5571999999999"]


# ── conteudo das mensagens ──────────────────────────────────────────────────
def test_aborto_diz_o_dia_a_unidade_e_o_erro(monkeypatch):
    _configura(monkeypatch, APP_BASE_URL="https://radiobras.benitechlab.com")
    espia = _Espia()
    notificador.avisar_aborto("18/08/2026", "388336",
                              "net::ERR_TUNNEL_CONNECTION_FAILED no login",
                              execucao_id=4242, _post=espia)
    t = espia.chamadas[0]["payload"]["text"]
    assert "18/08/2026" in t and "388336" in t
    assert "TUNNEL" in t
    # deep-link clicavel pro log da execucao (senao o aviso nao serve pra agir)
    assert "https://radiobras.benitechlab.com/relatorios/execucao/4242/log" in t
    # o dia inteiro parou: o aviso tem que deixar isso obvio
    assert "não faturou" in t.lower() or "nao faturou" in t.lower()


def test_resumo_da_rodada_lista_as_guias(monkeypatch):
    _configura(monkeypatch)
    espia = _Espia()
    notificador.avisar_falhas_da_rodada("18/08/2026", "388336", [
        {"gto": "195831154", "paciente": "ALESSANDRA", "motivo": "gemini: 503"},
        {"gto": "195904169", "paciente": "RAFAELA", "motivo": "anexação falhou"},
    ], _post=espia)
    t = espia.chamadas[0]["payload"]["text"]
    assert "195831154" in t and "195904169" in t
    assert "ALESSANDRA" in t and "RAFAELA" in t
    assert "2" in t                      # diz quantas


def test_resumo_vazio_nao_manda_mensagem(monkeypatch):
    _configura(monkeypatch)
    espia = _Espia()
    assert notificador.avisar_falhas_da_rodada("18/08/2026", "388336", [],
                                               _post=espia) is False
    assert espia.chamadas == []


def test_esgotou_e_escalacao_explicita(monkeypatch):
    _configura(monkeypatch)
    espia = _Espia()
    notificador.avisar_esgotou("195831154", "ALESSANDRA", "18/08/2026", "388336",
                               "gemini: 503 UNAVAILABLE", tentativas=6, _post=espia)
    t = espia.chamadas[0]["payload"]["text"]
    assert "195831154" in t
    # tem que ficar claro que o try again ACABOU e agora depende de mim
    assert "não recuperou" in t.lower() or "nao recuperou" in t.lower()
    assert "6" in t
