"""Anexo corrompido nao e falha transitoria — re-tentar nao conserta arquivo.

Caso FABRICIO DOS SANTOS SOUZA NASCIMENTO (196307916, 410923, 18/08). Os dois anexos
do prontuario dele resistiram a TODOS os caminhos de leitura, em QUATRO rodadas com o
mesmo erro byte a byte:

    FABRICIO DOS SANTOS SOUZA120260818.jpg   Pillow nao abre, MuPDF nao abre
    FABRICIO DOS SANTOS SOUZA.pdf            sem '%PDF' no inicio, MuPDF nao abre

    gemini: 400 INVALID_ARGUMENT — 'Unable to process input image'

O lado do exame esta completo: 6 arquivos no plano, laudos inclusive. So o PEDIDO nao
pode ser lido.

O erro caia em `_TRANSITORIO_RE` pelo prefixo generico `gemini\s*:` — e transitorio
significa "o loop de retry re-tenta". Mas 400 INVALID_ARGUMENT nao e instabilidade de
rede nem cota: e o servidor dizendo que o CONTEUDO enviado nao pode ser processado.
Nenhuma re-tentativa conserta um arquivo corrompido; a guia so queima as 6 tentativas
e some do painel enquanto o prazo corre.

O desfecho certo e Conferencia: os arquivos JA ESTAO na pasta da pendencia (feature de
22/08, pedido da Andrea). Um navegador e muito mais tolerante que Pillow/MuPDF — pode
ser que a pessoa consiga abrir e anexar a mao. Se estiver mesmo corrompido, ela ve
isso em dois segundos e pede o reenvio a clinica. As duas saidas sao melhores que um
retry que nunca vai funcionar.

500/503/UNAVAILABLE/timeout continuam transitorios: ali insistir FUNCIONA."""
import db


_CORROMPIDO = ("NÃO FATUROU por falha técnica na leitura dos documentos. Detalhe: "
               "gemini: 400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': "
               "'Unable to process input image. Please retry or report'}}")
_INSTAVEL = ("NÃO FATUROU por falha técnica na leitura dos documentos. Detalhe: "
             "gemini: 503 UNAVAILABLE")
_QUOTA = ("NÃO FATUROU porque a leitura automática ficou indisponível: os créditos "
          "da API de leitura acabaram.")


# ── o caso ────────────────────────────────────────────────────────────────
def test_conteudo_invalido_nao_e_transitorio():
    assert db.classe_retry(_CORROMPIDO, "erro") != "transitorio"


def test_conteudo_invalido_sai_do_retry():
    assert db.deve_entrar_no_retry(_CORROMPIDO, "erro") is False


def test_conteudo_invalido_vai_para_CONFERENCIA_e_aparece():
    """Os arquivos estao na pasta da guia — da para conferir e anexar a mao."""
    chave, quem, acao = db.classificar_pendencia(_CORROMPIDO, "erro")
    assert quem == "Conferência"
    assert db.eh_pendencia_front(_CORROMPIDO, "erro") is True
    assert db.eh_nosso(_CORROMPIDO, "erro") is False


# ── instabilidade de verdade continua NOSSA e no retry ────────────────────
def test_503_continua_transitorio():
    assert db.classe_retry(_INSTAVEL, "erro") == "transitorio"
    assert db.deve_entrar_no_retry(_INSTAVEL, "erro") is True
    assert db.eh_nosso(_INSTAVEL, "erro") is True


def test_quota_continua_nossa():
    """Sem credito nao e culpa do documento — e nossa, e some do painel."""
    assert db.eh_nosso(_QUOTA, "erro") is True
    assert db.eh_pendencia_front(_QUOTA, "erro") is False


def test_timeout_continua_transitorio():
    m = "NÃO FATUROU por falha técnica. Detalhe: gemini: timed out"
    assert db.classe_retry(m, "erro") == "transitorio"


def test_400_de_outro_tipo_nao_e_arrastado():
    """So o INVALID_ARGUMENT de conteudo. Um 400 generico segue como antes."""
    m = "NÃO FATUROU por falha técnica. Detalhe: gemini: 400 Bad Request"
    assert db.classe_retry(m, "erro") == "transitorio"
