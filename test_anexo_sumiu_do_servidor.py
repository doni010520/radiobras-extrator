"""Anexo que o PRORADIS lista mas nao entrega: o arquivo SUMIU do servidor.

Caso FABRICIO DOS SANTOS SOUZA NASCIMENTO (196307916, 410923, 18/08). Passei o dia
tratando isso como arquivo corrompido — Pillow nao abre, MuPDF nao abre, Gemini
responde 400 'Unable to process input image'. Cheguei a criar um grupo
`anexo_corrompido` mandando a operadora abrir os arquivos na pasta da guia.

Estava errado. Baixando os dois anexos direto do PRORADIS, os dois vem com 1050 bytes
e comecam com '<div s'. E HTML — a pagina de erro do proprio PRORADIS:

    A PHP Error was encountered
    Severity: Notice    Message: Undefined variable: fullpath
    Filename: controllers/patients.php   Line Number: 944, 949, 951
    Severity: Warning   Message: file_get_contents(): Filename cannot be empty

O anexo EXISTE no cadastro (aparece na lista) mas o arquivo fisico nao esta mais no
servidor: `fullpath` vem indefinido e o `file_get_contents` recebe nome vazio.

Por isso as tres bibliotecas falharam — as tres receberam HTML. E por isso a acao que
eu tinha escrito era impossivel de cumprir: a pasta da guia guardaria 1050 bytes de
erro de PHP.

Nenhuma re-tentativa traz de volta arquivo que nao esta no disco. Quem resolve e quem
pode ANEXAR DE NOVO no PRORADIS."""
import pytest

from esteira import preparar_anexo, _eh_pagina_de_erro
import db


_ERRO_PHP = (b'<div style="border:1px solid #990000;padding-left:20px;">\n'
             b'<h4>A PHP Error was encountered</h4>\n'
             b'<p>Message:  Undefined variable: fullpath</p>\n'
             b'<p>Filename: controllers/patients.php</p>\n</div>' + b' ' * 800)


# ── deteccao ──────────────────────────────────────────────────────────────
def test_reconhece_a_pagina_de_erro_do_proradis():
    assert _eh_pagina_de_erro(_ERRO_PHP) is True


def test_reconhece_html_generico():
    assert _eh_pagina_de_erro(b"<!DOCTYPE html><html><body>ops</body></html>") is True
    assert _eh_pagina_de_erro(b"<html><head><title>404</title></head>") is True


def test_nao_confunde_com_documento_de_verdade():
    import io
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (30, 30)).save(b, format="JPEG")
    assert _eh_pagina_de_erro(b.getvalue()) is False
    assert _eh_pagina_de_erro(b"%PDF-1.4\ncoisa") is False
    assert _eh_pagina_de_erro(b"") is False
    assert _eh_pagina_de_erro(None) is False


# ── preparar_anexo recusa com o motivo CERTO ──────────────────────────────
def test_pagina_de_erro_nao_vira_anexo():
    mime, motivo = preparar_anexo("FABRICIO DOS SANTOS SOUZA.pdf", _ERRO_PHP)
    assert mime is None
    assert "servidor" in motivo.lower() or "n\u00e3o est\u00e1" in motivo.lower()


def test_nao_cai_no_fallback_da_extensao():
    """O fallback pra extensao existe para arquivo que TALVEZ o Gemini leia. HTML
    ele nunca vai ler, e mandar so queima uma chamada e polui o diagnostico."""
    mime, _ = preparar_anexo("qualquer.jpg", _ERRO_PHP)
    assert mime is None


# ── a pendencia aponta para quem resolve ──────────────────────────────────
_MOTIVO = ("NÃO FATUROU porque o arquivo do anexo NÃO ESTÁ MAIS no servidor do "
           "PRORADIS: ele aparece na lista do prontuário, mas o download devolve "
           "erro do servidor em vez do documento.")


def test_vai_para_a_clinica_reanexar():
    chave, quem, acao = db.classificar_pendencia(_MOTIVO, "sem_solicitacao")
    assert chave == "anexo_sumiu"
    assert quem == "Clínica"
    a = acao.lower()
    assert "anex" in a and ("de novo" in a or "novamente" in a), acao


def test_sai_do_retry():
    """Nenhuma re-tentativa traz de volta arquivo que nao esta no disco."""
    assert db.deve_entrar_no_retry(_MOTIVO, "sem_solicitacao") is False
    assert db.eh_nosso(_MOTIVO, "sem_solicitacao") is False


def test_aparece_no_painel():
    assert db.eh_pendencia_front(_MOTIVO, "sem_solicitacao") is True


# ══════════════════════════════════════════════════════════════════════════
# O ENVELOPE de novo. Segunda vez no mesmo dia que uma causa conhecida e engolida
# por um texto generico — e o mesmo mecanismo: o classificador e first-match-wins e
# le o COMECO da frase.
#
# `_motivo_sem_candidatos` abre com "provavelmente falha temporaria da leitura"
# sempre que o prontuario tem documento e nenhum virou pedido. Isso e verdade quando
# o documento e manuscrito ou a leitura oscilou. NAO e verdade quando os arquivos
# sumiram do servidor: ali nao ha nada de temporario, e o retry queima tentativas
# contra um arquivo que nao volta.
#
# Medido no FABRICIO depois do primeiro conserto: chave='anexo_sumiu'/Clinica (certo),
# mas classe_retry='transitorio', eh_nosso=True e front=False — ou seja, continuava
# escondido do painel e no loop. A regra que faltava: se TODOS os anexos descartados
# sumiram do servidor, a causa e essa, e ela vai na frente.
# ══════════════════════════════════════════════════════════════════════════

from esteira import _motivo_sem_candidatos

_SUMIU = ("FABRICIO DOS SANTOS SOUZA120260818.jpg: o arquivo NÃO ESTÁ MAIS no "
          "servidor do PRORADIS")
_SUMIU2 = ("FABRICIO DOS SANTOS SOUZA.pdf: o arquivo NÃO ESTÁ MAIS no servidor "
           "do PRORADIS")
_ILEGIVEL = "OUTRO.jpg: formato .jpg não suportado ou arquivo corrompido"


def test_todos_sumiram_a_causa_vai_na_frente():
    m = _motivo_sem_candidatos(2, [_SUMIU, _SUMIU2])
    assert "falha tempor" not in m.lower(), m
    assert "NÃO ESTÁ MAIS no servidor" in m


def test_e_ai_sai_do_retry_e_aparece():
    m = _motivo_sem_candidatos(2, [_SUMIU, _SUMIU2])
    assert db.classe_retry(m, "sem_solicitacao") == "externo"
    assert db.deve_entrar_no_retry(m, "sem_solicitacao") is False
    assert db.eh_nosso(m, "sem_solicitacao") is False
    assert db.eh_pendencia_front(m, "sem_solicitacao") is True
    assert db.classificar_pendencia(m, "sem_solicitacao")[0] == "anexo_sumiu"


def test_mistura_continua_como_leitura():
    """Se UM anexo sumiu e outro so nao foi lido, ainda pode ser leitura nossa —
    re-tentar tem chance. Nao arrastar para 'sumiu'."""
    m = _motivo_sem_candidatos(2, [_SUMIU, _ILEGIVEL])
    assert "falha tempor" in m.lower()


def test_prontuario_vazio_nao_muda():
    m = _motivo_sem_candidatos(0, [])
    assert "nenhum pedido do dentista" in m
    assert db.classificar_pendencia(m, "sem_solicitacao")[1] == "Clínica"


def test_sem_descartados_nao_muda():
    m = _motivo_sem_candidatos(3, [])
    assert "falha tempor" in m.lower()


def test_categoria_erro_nao_reengole():
    """Mesmo com categoria='erro', a causa nomeada vence (como paciente_nao_achado)."""
    m = _motivo_sem_candidatos(2, [_SUMIU, _SUMIU2])
    assert db.eh_nosso(m, "erro") is False
