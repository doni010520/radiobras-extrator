"""Retry robusto da leitura de anexos da GTO (esteira._get_json_com_retry).

'Erros de leitura sao inadmissiveis' (dono, 10/08): um HTTP 500 transitorio do
OdontoPrev (TE-BFF-GTO-0001) ou um reset de conexao NAO pode fazer a guia cair em
NAO_VERIFICADA. O retry com backoff exponencial tem de absorver o hiccup.
"""
from esteira import _get_json_com_retry


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._p = payload
        self.text = text

    def json(self):
        return self._p


class _Sess:
    def __init__(self, respostas):
        self._r = list(respostas)
        self.chamadas = 0

    def get(self, url, timeout=None):
        self.chamadas += 1
        r = self._r.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _noop(_s):
    return None


def test_absorve_500_transitorio_e_retorna_json():
    sess = _Sess([_Resp(500, text="TE-BFF-GTO-0001"),
                  _Resp(500, text="TE-BFF-GTO-0001"),
                  _Resp(200, [{"nomeArquivo": "laudo.pdf"}])])
    js, falha = _get_json_com_retry(sess, "u", tentativas=6, _sleep=_noop)
    assert falha is None
    assert js == [{"nomeArquivo": "laudo.pdf"}]
    assert sess.chamadas == 3


def test_absorve_excecao_de_rede_e_depois_ok():
    sess = _Sess([ConnectionResetError("reset"), _Resp(200, [])])
    js, falha = _get_json_com_retry(sess, "u", tentativas=6, _sleep=_noop)
    assert falha is None and js == []


def test_status_nao_200_nunca_vira_lista_vazia():
    # regressao: um 500 NAO pode ser lido como '0 anexos' (mascarava o erro)
    sess = _Sess([_Resp(500, text="erro")] * 6)
    js, falha = _get_json_com_retry(sess, "u", tentativas=6, _sleep=_noop)
    assert js is None
    assert "500" in falha
    assert sess.chamadas == 6
