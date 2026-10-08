"""Guia JA_ANEXADA cujos eventos do portal nao puderam ser lidos nao sai "completa".

Casos TATH LORENA SILVA PINA (196792009) e ADRIANA QUELE REGIS DE SOUSA (196809496),
03/09: na mesma guia, uma rodada leu os exames e a seguinte gravou "nenhum". O GET de
/eventos/ficha era unico, sem retry, e o erro virava lista vazia; com lista vazia,
`_falta_no_portal` nao tem o que cobrar e a guia passa como faturada sem conferencia.
"""
import esteira


class _R:
    def __init__(self, status, js=None):
        self.status_code = status
        self._js = js
        self.text = ""

    def json(self):
        return self._js


class _Sess:
    def __init__(self, respostas):
        self.respostas = list(respostas)

    def get(self, url, timeout=None):
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _sem_espera(_s):
    pass


def test_le_os_exames_quando_o_portal_responde():
    s = _Sess([_R(200, [{"descricao": "Rad.Panor.S/Tra"}])])
    assert esteira._exames_ja_anexada(s, "1", _sleep=_sem_espera) == ["panoramica"]


def test_oscilacao_passageira_e_absorvida_pelo_retry():
    s = _Sess([_R(500), ConnectionError("reset"),
               _R(200, [{"descricao": "Doc Orto Compl"}])])
    assert "documentacao" in esteira._exames_ja_anexada(s, "1", _sleep=_sem_espera)


def test_falha_persistente_e_none_e_nao_lista_vazia():
    s = _Sess([_R(500)] * 4)
    assert esteira._exames_ja_anexada(s, "1", _sleep=_sem_espera) is None


def test_portal_responde_sem_eventos_e_lista_vazia_nao_none():
    s = _Sess([_R(200, [])])
    assert esteira._exames_ja_anexada(s, "1", _sleep=_sem_espera) == []
