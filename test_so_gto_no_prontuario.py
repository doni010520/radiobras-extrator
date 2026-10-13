"""Prontuario so com a propria GTO: falta o pedido (clinica), nao 'falha de leitura' (13/10)."""
import db
from esteira import _motivo_sem_candidatos


def test_so_a_gto_no_prontuario_e_falta_de_pedido():
    # LUCAS SANTOS DA SILVA 198305301: funil prontuario=1 cand=0, o unico anexo era a GTO
    m = _motivo_sem_candidatos(1, [], n_gto=1)
    assert "própria guia" in m and "reprocessar" not in m
    assert db.classificar_pendencia(m, "sem_solicitacao")[:2] == ("sem_pedido", "Clínica")
    assert not db.eh_nosso(m, "sem_solicitacao")


def test_documento_que_nao_e_gto_continua_como_leitura():
    m = _motivo_sem_candidatos(2, [], n_gto=1)
    assert "falha temporária da leitura" in m
