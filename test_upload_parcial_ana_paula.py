"""Upload que perde arquivo no meio nao pode voltar como `ok`.

Caso ANA PAULA VIEIRA DIAS (196933166, exame 02/09, rodada de 05/09 11:15). O robo
mandou tres arquivos:

    ENTREGA_faeb5a8511.jpg            <- a folha de imagem
    LAUDO_PANORAMICA_40348164_OFICIAL.pdf
    SOLICITACAO_0__ana_paula_dias_20260902_14245072.jpg

O log registrou "OK (3 enviados, 0 ja tinha)". A API do portal mostra que so o
LAUDO existe: os outros dois nunca chegaram. A guia foi dada por faturada SEM
IMAGEM — a documentacao incompleta que a operadora glosa com 3230.

A conferencia nao pegou porque lia o DOM: `_anexos_nomes` varre o body atras de
nome de arquivo e o `input[type=file]` EXIBE o que foi selecionado. Os chips do
seletor contem, por construcao, os tres enviados — o DOM sempre "confirma".

Aqui a pagina finge exatamente isso: mostra os tres chips, mas a API (a mesma que
a descoberta ja trata como autoritativa, injetada como `contar_fallback`) diz que
so um grudou."""
from extrator_odontoprev import upload_arquivos


class _Input:
    def __init__(self): self.arquivos = None
    def set_input_files(self, f): self.arquivos = f


class _El:
    def __init__(self, txt): self._t = txt
    def inner_text(self): return self._t


class _PaginaQueMostraOsChips:
    """DOM da popup: o contador reflete a verdade, mas a lista de nomes inclui os
    chips do seletor de arquivo — os que acabaram de ser escolhidos, tenham ou nao
    sido aceitos pelo servidor."""

    def __init__(self, anexos_reais, chips_apos_upload):
        self._reais = list(anexos_reais)
        self._chips = list(chips_apos_upload)
        self.input = _Input()

    def _visiveis(self):
        return self._reais + (self._chips if self.input.arquivos else [])

    def inner_text(self, _s):
        return f"total de anexos) : {len(self._reais)} sucesso"

    def query_selector(self, sel):
        return self.input if "file" in sel else None

    def query_selector_all(self, _s):
        return [_El(n) for n in self._visiveis()]

    def wait_for_timeout(self, _ms): pass


ENVIADOS = ["ENTREGA_faeb5a8511.jpg",
            "LAUDO_PANORAMICA_40348164_OFICIAL.pdf",
            "SOLICITACAO_0__ana_paula_dias_20260902_14245072.jpg"]


def _paths(tmp_path, nomes):
    saida = []
    for n in nomes:
        f = tmp_path / n
        f.write_bytes(b"x")
        saida.append(str(f))
    return saida


def test_dois_arquivos_perdidos_derrubam_o_ok(tmp_path):
    """O caso ANA PAULA: a API so ve o laudo, entao a guia NAO esta anexada."""
    pg = _PaginaQueMostraOsChips(
        anexos_reais=["imagemGTO"], chips_apos_upload=ENVIADOS)
    # depois do upload o portal aceitou UM: a GTO + o laudo
    def api():
        if pg.input.arquivos:
            return 2, {"imagemGTO", "LAUDO_PANORAMICA_40348164_OFICIAL.pdf"}
        return 1, {"imagemGTO"}

    r = upload_arquivos(pg, _paths(tmp_path, ENVIADOS), max_antes=1,
                        contar_fallback=api)

    assert r["ok"] is False, "upload parcial voltou como sucesso"
    assert r["nao_grudaram"] == ["ENTREGA_faeb5a8511.jpg",
                                 "SOLICITACAO_0__ana_paula_dias_20260902_14245072.jpg"]


def test_upload_inteiro_continua_passando(tmp_path):
    """A trava nao pode transformar envio bom em falha: sem isso, para tudo."""
    pg = _PaginaQueMostraOsChips(
        anexos_reais=["imagemGTO"], chips_apos_upload=ENVIADOS)
    def api():
        if pg.input.arquivos:
            return 4, {"imagemGTO", *ENVIADOS}
        return 1, {"imagemGTO"}

    r = upload_arquivos(pg, _paths(tmp_path, ENVIADOS), max_antes=1,
                        contar_fallback=api)

    assert r["ok"] is True
    assert r["nao_grudaram"] == []


def test_sem_api_e_sem_dom_nao_confirma(tmp_path):
    """Nao conseguir conferir por fonte nenhuma NAO e sucesso — vai para o retry,
    onde a idempotencia por _chave_anexo impede duplicar."""
    class _Cega(_PaginaQueMostraOsChips):
        def query_selector_all(self, _s): return []

    pg = _Cega(anexos_reais=["imagemGTO"], chips_apos_upload=ENVIADOS)
    r = upload_arquivos(pg, _paths(tmp_path, ENVIADOS), max_antes=1,
                        contar_fallback=lambda: (-1, set()))

    assert r["ok"] is False, "sem poder conferir, nao pode dizer que grudou"
