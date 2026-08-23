"""Responsavel COMPOSTO ('Clinica + Radiologista') tem de contar como externo.

Defeito achado por auditoria adversarial em 23/08, no mesmo dia em que o grupo
`sem_pedido_e_laudo` foi criado. `classe_retry` comparava por igualdade exata:

    if quem in ("Radiologista", "Clínica", "Cadastro"):
        return "externo"

'Clínica + Radiologista' nao esta nessa tupla, entao caia em 'logica'. Efeito na
tela: a EVELYN (196330383) — a guia de bloqueio DUPLO, a mais travada da fila,
precisando de acao de DOIS lados — virou tipo 'conferir' (sem ter documento nenhum
para conferir) e foi para o FIM do relatorio, atras de todas as que dependem de um
lado so.

Conferido por execucao sobre a Pendencia 960:
    com o sufixo -> ('sem_pedido_e_laudo','Clínica + Radiologista'), classe 'logica'
    sem o sufixo -> ('sem_pedido','Clínica'),                        classe 'externo'

A regra certa: se QUALQUER um dos responsaveis for externo, a guia espera terceiro —
re-tentar nao muda nada. Compor responsaveis nao pode mudar a natureza da espera."""
import db


_DUPLO = db.motivo_com_laudo_faltando(
    "NÃO FATUROU porque não há nenhum pedido do dentista anexado ao prontuário "
    "deste paciente.", True)


def test_composto_e_externo():
    assert db.classe_retry(_DUPLO, "sem_solicitacao") == "externo"


def test_composto_nao_entra_no_retry():
    """Nem a clinica anexa nem o radiologista lauda por re-tentativa."""
    assert db.deve_entrar_no_retry(_DUPLO, "sem_solicitacao") is False


def test_composto_aparece_no_painel():
    assert db.eh_pendencia_front(_DUPLO, "sem_solicitacao") is True
    assert db.eh_nosso(_DUPLO, "sem_solicitacao") is False


def test_a_chave_continua_a_do_bloqueio_duplo():
    chave, quem, _ = db.classificar_pendencia(_DUPLO, "sem_solicitacao")
    assert chave == "sem_pedido_e_laudo"
    assert "Clínica" in quem and "Radiologista" in quem


# ── os simples nao podem mudar ────────────────────────────────────────────
def test_clinica_sozinha_continua_externo():
    m = "NÃO FATUROU porque não há nenhum pedido do dentista anexado ao prontuário."
    assert db.classe_retry(m, "sem_solicitacao") == "externo"


def test_radiologista_sozinho_continua_externo():
    m = "Solicitação OK, mas falta o LAUDO válido no PRORADIS"
    assert db.classe_retry(m, "sem_laudo") == "externo"


def test_falha_nossa_continua_transitoria():
    m = "NÃO FATUROU por falha técnica na leitura dos documentos. Detalhe: gemini: 503"
    assert db.classe_retry(m, "erro") == "transitorio"


def test_conferencia_continua_logica():
    """Conferencia precisa de olho humano, nao de retry nem de terceiro."""
    m = ("NÃO FATUROU porque a solicitação do dentista não pôde ser confirmada")
    assert db.classe_retry(m, "sem_solicitacao") == "logica"
