"""Token vencido tem que ser RENOVADO, nao re-tentado.

Item 5 da lista da Andrea, com print do portal: cinco guias de 17/08 (conta 388336)
barradas com

    Documentacao OK, mas a anexacao falhou: nao consegui ler quantos anexos a guia
    ja tem (DOM e API falharam: HTTP 401 'Jwt is expired') — nada foi enviado, por
    seguranca

E ela reclamou com razao: *"Sem anexo nenhum na paciente ANDREIA CLARINDA, pq nao
leu?"*. O print do OdontoPrev mostra a guia dela com 1 anexo, enviado 17/08 10:14.
O documento estava la. O robo e que nao conseguiu contar.

A trava em si esta CERTA e nao se mexe: sem saber quantos anexos a guia ja tem, nao
se anexa (o portal nao permite remover anexo — duplicar e dano permanente).

O defeito e antes dela. O Bearer e capturado UMA VEZ, no login da descoberta, e
reusado o resto da rodada. Quando a anexacao roda 20+ minutos depois — o que e comum
sob throttle — o token ja venceu. Ai `_anexos_via_api` re-tenta 3x com backoff... com
o MESMO token morto. Tres 401 identicos, e a guia cai.

Insistir num token vencido nao o desvence. Em 401, renova."""
import pytest

import esteira


class _R:
    def __init__(self, code, payload=None, texto=""):
        self.status_code = code; self._p = payload; self.text = texto
    def json(self):
        return self._p


class _Sess:
    """Sessao falsa: responde 401 enquanto o header nao trouxer o token novo."""
    def __init__(self, bom="Bearer NOVO", respostas=None):
        self.headers = {}
        self.bom = bom
        self.chamadas = []
        self.proxies = {}
    def get(self, url, timeout=None):
        tok = self.headers.get("Authorization")
        self.chamadas.append(tok)
        if tok != self.bom:
            return _R(401, texto="Jwt is expired")
        return _R(200, [{"nomeArquivo": "imagemGTO", "imagemGTO": True}])


@pytest.fixture()
def sess(monkeypatch):
    s = _Sess()
    monkeypatch.setattr(esteira.requests, "Session", lambda: s)
    monkeypatch.setattr(esteira.time, "sleep", lambda *_: None)
    return s


# ── o caso ────────────────────────────────────────────────────────────────
def test_401_renova_o_token_e_recupera(sess):
    chamou = {"n": 0}

    def _renovar():
        chamou["n"] += 1
        return "Bearer NOVO"

    n, nomes, err = esteira._anexos_via_api("Bearer VELHO", "196264444",
                                            renovar=_renovar)
    assert n == 1, err
    assert err is None
    assert chamou["n"] == 1, "renovou uma vez so"


def test_sem_renovar_continua_falhando_como_antes(sess):
    """Sem o callable, o comportamento e exatamente o de antes — nada muda para
    quem chama sem passar renovar."""
    n, _, err = esteira._anexos_via_api("Bearer VELHO", "196264444")
    assert n == -1
    assert "401" in (err or "")


def test_nao_renova_em_erro_que_nao_e_de_token(monkeypatch):
    """500/timeout nao sao problema de credencial: renovar ali so gasta um login."""
    class _S500(_Sess):
        def get(self, url, timeout=None):
            self.chamadas.append(self.headers.get("Authorization"))
            return _R(500, texto="boom")
    s = _S500()
    monkeypatch.setattr(esteira.requests, "Session", lambda: s)
    monkeypatch.setattr(esteira.time, "sleep", lambda *_: None)
    chamou = {"n": 0}
    esteira._anexos_via_api("Bearer VELHO", "1",
                            renovar=lambda: (chamou.__setitem__("n", chamou["n"] + 1), "x")[1])
    assert chamou["n"] == 0


def test_renova_uma_vez_so_mesmo_com_varios_401(sess):
    """Se a renovacao devolver outro token morto, nao entra em loop de login."""
    chamou = {"n": 0}

    def _renovar():
        chamou["n"] += 1
        return "Bearer AINDA_MORTO"

    n, _, err = esteira._anexos_via_api("Bearer VELHO", "1", renovar=_renovar)
    assert n == -1
    assert chamou["n"] == 1


def test_renovacao_que_falha_nao_derruba(sess):
    """Login novo pode falhar (proxy, rate-limit). Tem de degradar para o
    comportamento antigo, nunca levantar dentro do worker de anexacao."""
    def _renovar():
        raise RuntimeError("login falhou")
    n, _, err = esteira._anexos_via_api("Bearer VELHO", "1", renovar=_renovar)
    assert n == -1 and isinstance(err, str)


def test_renovacao_que_devolve_vazio_nao_derruba(sess):
    n, _, err = esteira._anexos_via_api("Bearer VELHO", "1", renovar=lambda: None)
    assert n == -1 and isinstance(err, str)


# ── a trava de duplicidade nao pode ceder ─────────────────────────────────
def test_contagem_negativa_continua_bloqueando_a_anexacao():
    """A regra do dono: sem saber quantos anexos a guia tem, nao anexa. O portal
    nao remove anexo — duplicar e irreversivel."""
    from extrator_odontoprev import upload_arquivos
    import inspect
    src = inspect.getsource(upload_arquivos)
    assert "antes is None or antes < 0" in src
    assert "nao foi possivel LER quantos anexos" in src
