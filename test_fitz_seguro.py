"""PyMuPDF (fitz) nao suporta uso por varias threads; a esteira roda varios leitores
em paralelo. Em 10/10 um processo de teste caiu com 'Windows fatal exception: access
violation' dentro de get_text numa thread de leitor. Queda nativa mata o processo
inteiro: a rodada morre sem gravar nada, sem rastro no banco.

Regra: toda funcao que usa fitz roda sob uma trava unica (fitz_seguro.com_fitz)."""
import ast
import os
import threading
import time

import fitz_seguro

ARQS = [f for f in os.listdir(".") if f.endswith(".py")
        and not f.startswith(("test_", "replay_")) and f != "fitz_seguro.py"]


def test_funcoes_decoradas_nunca_rodam_ao_mesmo_tempo():
    dentro, pico = [0], [0]
    lock = threading.Lock()

    @fitz_seguro.com_fitz
    def usa_pdf():
        with lock:
            dentro[0] += 1
            pico[0] = max(pico[0], dentro[0])
        time.sleep(0.02)
        with lock:
            dentro[0] -= 1

    ts = [threading.Thread(target=usa_pdf) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert pico[0] == 1


def test_trava_reentrante_para_funcao_decorada_chamar_outra():
    @fitz_seguro.com_fitz
    def interna():
        return "ok"

    @fitz_seguro.com_fitz
    def externa():
        return interna()

    assert externa() == "ok"


def _decorada(n):
    return any((isinstance(d, ast.Name) and d.id == "com_fitz") or
               (isinstance(d, ast.Attribute) and d.attr == "com_fitz")
               for d in n.decorator_list)


def test_toda_funcao_que_usa_fitz_tem_a_trava():
    faltando = []
    for f in ARQS:
        src = open(f, encoding="utf-8").read()
        if "fitz" not in src:
            continue
        for n in ast.parse(src).body:
            if isinstance(n, ast.FunctionDef):
                seg = ast.get_source_segment(src, n) or ""
                if ("fitz." in seg or "import fitz" in seg) and not _decorada(n):
                    faltando.append(f"{f}:{n.name}")
    assert not faltando, f"usam PyMuPDF sem a trava: {faltando}"
