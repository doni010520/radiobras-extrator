"""Guia parada por DOIS motivos nao pode reportar so um deles.

Caso EVELYN DE JESUS SANTOS (GTO 196330383, 388336, 18/08), achado na auditoria de
23/08. O log da propria execucao ja sabia a verdade:

    [DEC1] 196330383 ... | laudo+img=1 | FALTA: LAUDO+SOLICITACAO/JUSTIFICATIVA

Faltam os DOIS: o pedido (da clinica) e o laudo da panoramica (NOSSO — o unico
arquivo do plano e um ENTREGA_*.jpg, nenhum LAUDO_* apareceu em 4 rodadas ao longo
de 2 dias). Mas a cadeia de categorizacao em esteira.py e excludente (if/elif): sem
solicitacao vence, `sem_laudo` nunca e alcancado, e o motivo gravado fala so do
pedido. O classificador le esse texto e responde "Clinica".

O custo disso nao e cosmetico. A operacao cobra a clinica; a clinica anexa o pedido;
a guia roda de novo dias depois e SO ENTAO descobre que falta o nosso laudo — que
ninguem pediu, porque ninguem sabia. Duas viagens em vez de uma, com o prazo de
faturamento correndo.

A categoria continua `sem_solicitacao` (e o bloqueio que a clinica resolve), mas o
motivo tem de listar os dois e a pendencia tem de sair com os DOIS responsaveis."""
import db


_SO_PEDIDO = ("NÃO FATUROU porque não há nenhum pedido do dentista anexado ao "
              "prontuário deste paciente. O sistema abriu o prontuário e não "
              "encontrou nenhum documento que sirva como pedido. O QUE FAZER: "
              "pedir à clínica que anexe o pedido no prontuário.")


# ── o texto passa a dizer as duas coisas ───────────────────────────────────
def test_sem_laudo_o_motivo_nao_muda():
    """Controle: guia so sem pedido continua exatamente como estava."""
    assert db.motivo_com_laudo_faltando(_SO_PEDIDO, False) == _SO_PEDIDO


def test_com_laudo_faltando_o_motivo_cita_os_dois():
    m = db.motivo_com_laudo_faltando(_SO_PEDIDO, True)
    assert _SO_PEDIDO in m, "nao pode perder o texto do pedido"
    assert "LAUDO" in m
    assert "não sai só com o pedido" in m.lower()


def test_motivo_vazio_nao_inventa():
    assert db.motivo_com_laudo_faltando("", False) == ""
    assert "LAUDO" in db.motivo_com_laudo_faltando("", True)


# ── e a pendencia sai com os DOIS donos ────────────────────────────────────
def test_classificador_reconhece_o_bloqueio_duplo():
    m = db.motivo_com_laudo_faltando(_SO_PEDIDO, True)
    chave, quem, acao = db.classificar_pendencia(m, "sem_solicitacao")
    assert chave == "sem_pedido_e_laudo"
    assert "Clínica" in quem and "Radiologista" in quem
    assert "laudo" in acao.lower() and "pedido" in acao.lower()


def test_sem_pedido_puro_continua_da_clinica():
    """A regra nova nao pode capturar as 6 guias que sao mesmo so da clinica
    (MARCIO, GILDASIO, EMILY, KAUAN x2, LARISSA — todas com laudo pronto)."""
    chave, quem, _ = db.classificar_pendencia(_SO_PEDIDO, "sem_solicitacao")
    assert chave == "sem_pedido"
    assert quem == "Clínica"


def test_bloqueio_duplo_nao_e_falha_nossa_e_aparece_no_painel():
    """Metade e da clinica: tem de ficar visivel para a operacao, e fora do retry
    (re-tentar nao faz nem a clinica anexar nem o radiologista laudar)."""
    m = db.motivo_com_laudo_faltando(_SO_PEDIDO, True)
    assert db.eh_nosso(m, "sem_solicitacao") is False
    assert db.eh_pendencia_front(m, "sem_solicitacao") is True
    assert db.deve_entrar_no_retry(m, "sem_solicitacao") is False
