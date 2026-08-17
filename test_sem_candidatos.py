"""P0: 'sem pedido' (candidatos=0) só pode acusar a clínica quando o prontuário
está REALMENTE vazio. Se há documentos mas nenhum foi reconhecido como pedido, a
causa provável é leitura nossa (manuscrito/503 intermitente) — não 'a clínica não
anexou'. Trace 17/08: KAUA/ALINE tinham o pedido no prontuário (cand=2) e saíam
'não há NENHUM documento'; na releitura saíram 'auto'."""
from esteira import _motivo_sem_candidatos
from db import classe_retry


def test_prontuario_vazio_e_da_clinica():
    m = _motivo_sem_candidatos(0, [])
    assert "clínica" in m.lower()
    # prontuário vazio de verdade = espera a clínica anexar (externo, sem retry)
    assert classe_retry(m) == "externo"


def test_docs_no_prontuario_mas_nenhum_pedido_e_nossa_e_retentavel():
    m = _motivo_sem_candidatos(3, [])
    # a mensagem NÃO pode mentir dizendo que não há documento nenhum
    assert "não encontrou nenhum documento" not in m.lower()
    assert "3" in m
    # com documentos presentes, provável leitura nossa -> reprocessar (transitório)
    assert classe_retry(m) == "transitorio"


def test_um_doc_no_prontuario_tambem_e_nossa():
    # NICOLLY: só o RG dela; ainda assim "não anexou nada" seria falso -> reprocessa
    m = _motivo_sem_candidatos(1, [])
    assert classe_retry(m) == "transitorio"
