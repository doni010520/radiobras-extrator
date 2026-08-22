"""DISJUNTOR: falha GLOBAL nao pode queimar o retry de guia nenhuma.

Incidente de 22/08 que motivou isto: a banda do proxy residencial acabou as 08:31.
As 13 guias do dia 18/08 entraram na fila e gastaram as 6 tentativas — todas contra
o mesmo proxy morto, nenhuma por causa propria. As 13 esgotaram o teto, mandaram 13
mensagens em 2 minutos e sairam do loop automatico: quando o proxy voltou, nenhuma
delas voltou sozinha. O retry, que existe pra evitar trabalho manual, criou 13.

Regra: "esta guia falhou" e "o mundo esta fora do ar" nao sao a mesma coisa.
Falha global -> devolve a tentativa, PAUSA a fila e manda UMA mensagem."""
import db


_PROXY = ("Documentação OK, mas a anexação falhou: nao consegui ler quantos anexos "
          "a guia ja tem (DOM e API falharam: ProxyError: HTTPSConnectionPool("
          "host='gto-credenciado.odontoprev.com.br', port=443): Max retries "
          "exceeded with u) — nada foi enviado, por seguranca")
_LOGIN = ("Não foi possível conectar ao OdontoPrev pelo proxy (código 410923) — NÃO "
          "é a senha do portal. É o proxy residencial que dá acesso ao OdontoPrev")


# ── o que conta como falha GLOBAL ───────────────────────────────────────────
def test_proxy_morto_e_falha_global():
    assert db.eh_falha_global(_PROXY) is True
    assert db.eh_falha_global(_LOGIN) is True


def test_variacoes_de_proxy_e_login():
    for m in ("ProxyError('Cannot connect to proxy'",
              "Max retries exceeded with url: /v1/gto",
              "Falha no login — URL pos-submit: /ris/login",
              "proxy retornou 403 Forbidden no CONNECT"):
        assert db.eh_falha_global(m) is True, m


def test_falha_da_GUIA_nao_e_global():
    # estas sao por-guia: retentar faz sentido, o mundo esta de pe
    for m in ("gemini: 503 UNAVAILABLE",
              "Jwt is expired",
              "nenhum documento do prontuário está no nome deste paciente",
              "falta o LAUDO do radiologista",
              "Timeout 30000ms exceeded"):
        assert db.eh_falha_global(m) is False, m


def test_vazio_nao_e_global():
    assert db.eh_falha_global("") is False
    assert db.eh_falha_global(None) is False


# ── a leitura da RODADA: so e global se NADA faturou ────────────────────────
def test_rodada_toda_de_proxy_e_apagao():
    # 13 itens, nenhum faturou, todos com assinatura de proxy -> apagao
    itens = [{"faturado": False, "motivo": _PROXY} for _ in range(13)]
    assert db.rodada_foi_apagao(itens) is True


def test_se_alguma_faturou_NAO_e_apagao():
    # se uma passou, a infra estava de pe — a falha das outras e por-guia
    itens = [{"faturado": True, "motivo": ""},
             {"faturado": False, "motivo": _PROXY}]
    assert db.rodada_foi_apagao(itens) is False


def test_falha_mista_NAO_e_apagao():
    # proxy numa e falta de laudo noutra: o mundo esta de pe
    itens = [{"faturado": False, "motivo": _PROXY},
             {"faturado": False, "motivo": "falta o LAUDO do radiologista"}]
    assert db.rodada_foi_apagao(itens) is False


def test_uma_guia_sozinha_com_proxy_NAO_pausa_tudo():
    # uma andorinha so nao faz apagao — exige pelo menos 2 pra pausar a fila
    itens = [{"faturado": False, "motivo": _PROXY}]
    assert db.rodada_foi_apagao(itens) is False


def test_rodada_vazia_nao_e_apagao():
    assert db.rodada_foi_apagao([]) is False
    assert db.rodada_foi_apagao(None) is False


# ── a orquestracao: o que o worker faz quando reconhece o apagao ────────────
import esteira        # noqa: E402


def _cenario(monkeypatch, itens_por_rodada, grupos):
    """Fakes do worker. `grupos` = o que retries_devidos devolve."""
    reg = {"bump": [], "desfaz": [], "pausas": [], "avisos": [], "rodadas": []}
    monkeypatch.setattr(db, "retries_devidos", lambda limite=50: grupos)
    monkeypatch.setattr(db, "bump_retry", lambda g: reg["bump"].append(g))
    monkeypatch.setattr(db, "desfazer_bump", lambda g: reg["desfaz"].append(g))
    monkeypatch.setattr(db, "get_portal_senha", lambda c: "senha")
    monkeypatch.setattr(db, "salvar_execucao", lambda r, l: 1)
    monkeypatch.setattr(db, "retry_pausado", lambda: False)
    monkeypatch.setattr(db, "pausar_retry",
                        lambda minutos, motivo: reg["pausas"].append((minutos, motivo)))

    import notificador
    monkeypatch.setattr(notificador, "avisar_pausa",
                        lambda *a, **k: reg["avisos"].append((a, k)) or True)

    def _fake(dia, *a, **kw):
        reg["rodadas"].append((dia, kw.get("conta")))
        return {"data": dia, "conta": kw.get("conta"), "dry_run": False,
                "decisoes": itens_por_rodada}
    monkeypatch.setattr(esteira, "rodar_esteira", _fake)
    return reg


_APAGAO = [{"gto": "1", "faturado": False, "motivo": _PROXY},
           {"gto": "2", "faturado": False, "motivo": _PROXY}]
_NORMAL = [{"gto": "1", "faturado": True, "motivo": ""},
           {"gto": "2", "faturado": False, "motivo": "falta o LAUDO"}]

_DOIS_GRUPOS = [
    {"gto": "1", "conta": "388336", "dia": "18/08/2026", "tentativas": 0},
    {"gto": "2", "conta": "388336", "dia": "18/08/2026", "tentativas": 0},
    {"gto": "9", "conta": "397950", "dia": "17/08/2026", "tentativas": 0},
]


def test_apagao_devolve_a_tentativa_das_guias(monkeypatch):
    """O ponto central: a guia nao pode pagar por uma queda que nao e dela."""
    reg = _cenario(monkeypatch, _APAGAO, _DOIS_GRUPOS)
    esteira.processar_retries()
    assert reg["bump"], "tem que contar a tentativa antes (protege de travamento)"
    # ...e devolver todas as do grupo que caiu no apagao
    assert set(reg["desfaz"]) >= {"1", "2"}


def test_apagao_pausa_a_fila_e_para_a_varredura(monkeypatch):
    # sem isso, o worker seguiria pro grupo seguinte e queimaria aquelas tambem
    reg = _cenario(monkeypatch, _APAGAO, _DOIS_GRUPOS)
    esteira.processar_retries()
    assert len(reg["pausas"]) == 1
    assert len(reg["rodadas"]) == 1, "parou no 1o grupo, nao varreu o resto"


def test_apagao_manda_UMA_mensagem_so(monkeypatch):
    # 22/08: foram 13 mensagens em 2 minutos, uma por guia. Nunca mais.
    reg = _cenario(monkeypatch, _APAGAO, _DOIS_GRUPOS)
    esteira.processar_retries()
    assert len(reg["avisos"]) == 1


def test_rodada_normal_nao_pausa_nem_devolve(monkeypatch):
    # o caminho comum NAO pode regredir: falha de guia segue contando tentativa
    reg = _cenario(monkeypatch, _NORMAL, _DOIS_GRUPOS)
    esteira.processar_retries()
    assert reg["desfaz"] == []
    assert reg["pausas"] == []
    assert reg["avisos"] == []
    assert len(reg["rodadas"]) == 2, "varreu os dois grupos normalmente"


def test_fila_pausada_nem_comeca(monkeypatch):
    reg = _cenario(monkeypatch, _NORMAL, _DOIS_GRUPOS)
    monkeypatch.setattr(db, "retry_pausado", lambda: True)
    esteira.processar_retries()
    assert reg["rodadas"] == []
    assert reg["bump"] == []
