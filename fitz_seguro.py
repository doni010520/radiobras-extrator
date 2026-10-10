"""Trava unica para o PyMuPDF (fitz).

O PyMuPDF nao suporta uso por varias threads, e a esteira roda varios leitores e
baixadores em paralelo. Em 10/10 um processo caiu com 'Windows fatal exception:
access violation' dentro de get_text numa thread de leitor. Queda NATIVA mata o
processo inteiro: a rodada morre sem gravar nada no banco, sem rastro.

Toda funcao que usa fitz e decorada com @com_fitz: so uma thread mexe no PyMuPDF por
vez. O custo e desprezivel (PDF em milissegundos; o gargalo e o Gemini, em
segundos). RLock porque uma funcao decorada pode chamar outra decorada.
test_fitz_seguro.py varre o codigo e falha se aparecer uso de fitz sem a trava."""
import functools
import threading

TRAVA_FITZ = threading.RLock()


def com_fitz(f):
    @functools.wraps(f)
    def _com_trava(*a, **k):
        with TRAVA_FITZ:
            return f(*a, **k)
    return _com_trava
