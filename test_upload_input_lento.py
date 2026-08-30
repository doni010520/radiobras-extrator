"""O input de upload as vezes demora a renderizar — e o robo desistia em 1 segundo.

Casos JEFTE MOTA SILVA JUNIOR (196499739, 27/08) e GIOVANA DOS SANTOS SILVA
(30/08): "Documentacao OK, mas a anexacao falhou: input[type=file] de upload nao
encontrado na GTO". Nos dois a documentacao estava pronta e a guia so nao faturou
por isso; o JEFTE passou na re-tentativa 24 minutos depois, o que prova que o
elemento aparece — so nao no primeiro segundo.

O codigo clicava em UPLOAD, esperava 1000ms fixos e desistia. Aqui ele passa a
insistir por alguns segundos, como ja faz no resto do arquivo (a espera dos anexos
renderizarem, a verificacao por nome com polling)."""
import os

from extrator_odontoprev import upload_arquivos


class _Input:
    def __init__(self): self.arquivos = None
    def set_input_files(self, f): self.arquivos = f


class _PaginaLenta:
    """input[type=file] so aparece depois de N consultas."""

    def __init__(self, aparece_na=4, n_anexos=1):
        self._n = n_anexos
        self._consultas = 0
        self._aparece_na = aparece_na
        self.input = _Input()
        self.nomes_apos_upload = set()

    def inner_text(self, _s):
        base = f"total de anexos) : {self._n}"
        return base + (" " + " ".join(self.nomes_apos_upload)
                       if self.nomes_apos_upload else "")

    def query_selector(self, sel):
        if "file" not in sel:
            return None
        self._consultas += 1
        if self._consultas >= self._aparece_na:
            return self.input
        return None

    def query_selector_all(self, _s): return []
    def wait_for_timeout(self, _ms): pass


def test_espera_o_input_aparecer_em_vez_de_desistir(tmp_path):
    f = tmp_path / "LAUDO_PANORAMICA_40344815_OFICIAL.pdf"
    f.write_bytes(b"x")
    pg = _PaginaLenta(aparece_na=4)
    r = upload_arquivos(pg, [str(f)], max_antes=1)
    assert pg.input.arquivos == [str(f)], "desistiu antes de o input renderizar"
    assert "input[type=file]" not in str(r.get("erro") or "")


def test_input_que_nunca_aparece_ainda_falha(tmp_path):
    """A insistencia tem fim: guia sem campo de upload continua sendo erro."""
    f = tmp_path / "LAUDO_PANORAMICA_1_OFICIAL.pdf"
    f.write_bytes(b"x")
    pg = _PaginaLenta(aparece_na=10**6)
    try:
        upload_arquivos(pg, [str(f)], max_antes=1)
        assert False, "deveria ter falhado"
    except RuntimeError as e:
        assert "input[type=file]" in str(e)
