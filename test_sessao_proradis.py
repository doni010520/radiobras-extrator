"""Sessao do PRORADIS que cai no meio da rodada (10/10).

Deslogado, toda consulta devolve a tela de login e o robo le isso como "nada
encontrado": a guia virava SEM_MATCH ("nome escrito diferente") ou SEM_ARQUIVOS
("cobrar o laudo do radiologista"), sem ser verdade. A causa medida foi a impressao
do modelo (consertada), mas qualquer causa produz o mesmo estrago. Agora: guia que
deu "nao achei" com a sessao morta -> reloga e refaz uma vez; sem login -> falha
tecnica nossa (retry), nunca culpa da clinica."""
import esteira
import extrator_arquivos as ea


class _Pg:
    def __init__(self, resposta):
        self.resposta = resposta

    def evaluate(self, js, *a):
        if isinstance(self.resposta, Exception):
            raise self.resposta
        return self.resposta


def test_sessao_ok_le_o_sinal_da_pagina():
    assert ea.sessao_proradis_ok(_Pg(True)) is True
    assert ea.sessao_proradis_ok(_Pg(False)) is False


def test_sessao_ok_sem_resposta_e_desconhecido_nao_falso():
    """Sem como perguntar (pagina falsa, erro de rede) nao se presume deslogado."""
    assert ea.sessao_proradis_ok(_Pg(None)) is None
    assert ea.sessao_proradis_ok(_Pg(RuntimeError("x"))) is None


def _rodar(monkeypatch, resultados, sessoes, relogin_ok=True):
    chamadas = {"baixa": 0, "relogar": 0}
    fila_r, fila_s = list(resultados), list(sessoes)

    def baixa(*a, **k):
        chamadas["baixa"] += 1
        return dict(fila_r.pop(0))

    monkeypatch.setattr(esteira, "_baixa_um", baixa)

    def relogar():
        chamadas["relogar"] += 1
        return relogin_ok

    r = esteira._baixa_com_sessao(None, None, {}, {"gto": "1", "nome": "X"}, "tmp", "01/10/2026",
                                  _ok=lambda: fila_s.pop(0), _relogar=relogar)
    return r, chamadas


def test_sessao_viva_nao_muda_nada(monkeypatch):
    r, c = _rodar(monkeypatch, [{"status": "SEM_MATCH"}], [True, True])
    assert r["status"] == "SEM_MATCH" and c == {"baixa": 1, "relogar": 0}


def test_sessao_morta_antes_reloga_antes_de_baixar(monkeypatch):
    r, c = _rodar(monkeypatch, [{"status": "BAIXADO"}], [False])
    assert r["status"] == "BAIXADO" and c == {"baixa": 1, "relogar": 1}


def test_caiu_durante_a_guia_reloga_e_refaz(monkeypatch):
    r, c = _rodar(monkeypatch, [{"status": "SEM_ARQUIVOS"}, {"status": "BAIXADO"}],
                  [True, False, True])
    assert r["status"] == "BAIXADO" and c == {"baixa": 2, "relogar": 1}


def test_login_nao_volta_vira_falha_tecnica_e_nao_nao_encontrado(monkeypatch):
    r, c = _rodar(monkeypatch, [{"status": "SEM_MATCH"}], [True, False], relogin_ok=False)
    assert r["status"] == "ERRO" and "falha técnica" in r["erro"]
    assert c["baixa"] == 1


def test_baixado_nao_consulta_sessao_de_novo(monkeypatch):
    r, c = _rodar(monkeypatch, [{"status": "BAIXADO"}], [True])
    assert r["status"] == "BAIXADO" and c == {"baixa": 1, "relogar": 0}


# ── leitura (_decidir) tambem usa a sessao para abrir o prontuario ───────────────
def _decidir_rodar(monkeypatch, decisoes, sessoes, relogin_ok=True):
    c = {"decidir": 0, "relogar": 0}
    fila_d, fila_s = list(decisoes), list(sessoes)

    def decidir(*a, **k):
        c["decidir"] += 1
        return dict(fila_d.pop(0))

    monkeypatch.setattr(esteira, "_decidir", decidir)

    def relogar():
        c["relogar"] += 1
        return relogin_ok

    d = esteira._decidir_com_sessao(None, None, None, {"nome": "X"}, "p",
                                    _ok=lambda: fila_s.pop(0), _relogar=relogar)
    return d, c


def test_decisao_com_sessao_viva_fica(monkeypatch):
    d, c = _decidir_rodar(monkeypatch, [{"decisao": {"ok": 1}}], [True, True])
    assert d == {"decisao": {"ok": 1}} and c == {"decidir": 1, "relogar": 0}


def test_decisao_tomada_com_sessao_morta_e_refeita(monkeypatch):
    d, c = _decidir_rodar(monkeypatch,
                          [{"decisao": None, "erro": "anexos: nao encontrado no cadastro"},
                           {"decisao": {"ok": 1}}],
                          [True, False])
    assert d == {"decisao": {"ok": 1}} and c == {"decidir": 2, "relogar": 1}


def test_decisao_sem_login_vira_falha_tecnica(monkeypatch):
    d, c = _decidir_rodar(monkeypatch, [{"decisao": None, "erro": "x"}], [True, False],
                          relogin_ok=False)
    assert d["decisao"] is None and "falha técnica" in d["erro"]
