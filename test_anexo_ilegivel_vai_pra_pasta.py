"""Quando a leitura FALHA, os anexos do prontuario tem de ir para a pasta da guia.

Item 6 da Andrea, com as palavras dela: *"criar pasta com imagens resolvidas para
casos de NAO CONSEGUIR LER SOLICITACOES, depois ela anexa tudo"*. O recurso existe e
esta ligado — mas nao servia justamente o caso que o motivou.

Achado na reauditoria de 23/08, no FABRICIO DOS SANTOS SOUZA NASCIMENTO (196307916):
a pendencia dele manda "os arquivos estao no bloco Arquivos desta guia: abrir e
conferir", e os arquivos NAO ESTAO LA. Prova aritmetica no log da exec 692:
"[ARQ] 10 arquivo(s) de 3 guia(s)" = MARCIO 2 + HOSANA 2 + FABRICIO 6 — os 6 sao os
ENTREGAVEIS nossos (laudos e imagens), nenhum e o anexo do prontuario.

A causa: o bloco [ARQ] copia de `pasta_dl`, e a unica coisa do prontuario que chega
la e o `SOLICITACAO_*`, gravado dentro de `if candidato_valido:`. Quando a leitura
falha nao ha candidato validado — entao nada do prontuario e guardado. O recurso
servia todos os casos MENOS aquele para o qual foi pedido.

Agora, sem candidato validado, os anexos vao para a pasta com prefixo `NAO_LIDO_`.
Isso importa porque um navegador e muito mais tolerante que Pillow/MuPDF: o arquivo
que derrubou o robo pode abrir na tela dela. E se estiver mesmo corrompido, ela ve
isso em dois segundos e pede o reenvio — em vez de caçar um arquivo que nao existe."""
import os
import tempfile

import esteira


def test_salva_os_anexos_quando_nao_ha_candidato_valido():
    with tempfile.TemporaryDirectory() as d:
        cands = [("PEDIDO FABRICIO.jpg", "image/jpeg", b"\xff\xd8\xff-bytes-ruins", None),
                 ("PEDIDO FABRICIO.pdf", "application/pdf", b"%PDF-quebrado", None)]
        n = esteira._guardar_nao_lidos(d, cands)
        assert n == 2
        arqs = sorted(os.listdir(d))
        assert all(a.startswith("NAO_LIDO_") for a in arqs), arqs
        assert any("FABRICIO" in a for a in arqs)


def test_conteudo_e_preservado_byte_a_byte():
    """A operadora vai abrir o arquivo — tem de ser o original, nao uma conversao."""
    with tempfile.TemporaryDirectory() as d:
        blob = b"\x00\x01conteudo original\xfe"
        esteira._guardar_nao_lidos(d, [("x.jpg", "image/jpeg", blob, None)])
        f = os.path.join(d, sorted(os.listdir(d))[0])
        assert open(f, "rb").read() == blob


def test_nome_de_arquivo_e_sanitizado():
    with tempfile.TemporaryDirectory() as d:
        esteira._guardar_nao_lidos(d, [("../../etc/passwd", "image/jpeg", b"x", None)])
        arqs = os.listdir(d)
        assert len(arqs) == 1
        assert "/" not in arqs[0] and ".." not in arqs[0]


def test_indice_evita_colisao_de_nome():
    with tempfile.TemporaryDirectory() as d:
        esteira._guardar_nao_lidos(d, [("igual.jpg", "image/jpeg", b"a", None),
                                       ("igual.jpg", "image/jpeg", b"b", None)])
        assert len(os.listdir(d)) == 2


def test_pasta_inexistente_nao_quebra():
    """Guardar arquivo nunca pode derrubar um faturamento."""
    assert esteira._guardar_nao_lidos("/pasta/que/nao/existe", [("a.jpg", "i", b"x", None)]) == 0
    assert esteira._guardar_nao_lidos("", [("a.jpg", "i", b"x", None)]) == 0
    assert esteira._guardar_nao_lidos(None, None) == 0


def test_lista_vazia():
    with tempfile.TemporaryDirectory() as d:
        assert esteira._guardar_nao_lidos(d, []) == 0
        assert os.listdir(d) == []
