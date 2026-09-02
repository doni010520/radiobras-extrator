"""Fechar o ciclo: completar a guia incompleta sem duplicar o que ja esta la.

Ate aqui o robo detectava a guia incompleta, avisava e parava. Completar exige
resolver um problema especifico: a idempotencia do `upload_arquivos` e por NOME de
arquivo, e quem anexa a mao usa nome livre.

Caso JESSICA DA SILVA DOS SANTOS (196708276, 27/08): alguem anexou a mao em 01/09
um arquivo chamado "Laudo Cefalometrico.pdf". O laudo equivalente do robo se chama
"LAUDO_TELERRADIOGRAFIA_<acc>_CEPH.pdf". Os nomes nao batem, entao a idempotencia
por nome deixaria subir — e a guia ficaria com DOIS laudos do mesmo exame, num
portal que nao permite remover anexo.

A comparacao aqui e por TIPO DE EXAME. Conservadora nos dois sentidos: arquivo cujo
exame nao da para reconhecer nao sobe (nao arrisca duplicar), e exame ja coberto
nunca sobe de novo."""
from conferencia import arquivos_que_faltam


def test_nao_duplica_laudo_que_a_clinica_anexou_com_outro_nome():
    """O caso JESSICA. A tele ja esta na guia como 'Laudo Cefalometrico.pdf'."""
    no_portal = ["JESSICA DA SILVA DOS SANTOS.jpg", "Laudo Cefalométrico.pdf",
                 "image - 2026-09-01T115649.100.jpg", "imagemGTO"]
    disponiveis = ["LAUDO_PANORAMICA_40346451_OFICIAL.pdf",
                   "LAUDO_TELERRADIOGRAFIA_40346451_CEPH.pdf"]
    r = arquivos_que_faltam({"documentacao"}, no_portal, disponiveis)
    assert r == ["LAUDO_PANORAMICA_40346451_OFICIAL.pdf"], r


def test_sobe_o_laudo_que_falta_de_verdade():
    no_portal = ["ENTREGA_a.jpg", "imagemGTO"]
    disponiveis = ["LAUDO_PANORAMICA_1_OFICIAL.pdf"]
    r = arquivos_que_faltam({"panoramica"}, no_portal, disponiveis)
    assert r == ["LAUDO_PANORAMICA_1_OFICIAL.pdf"]


def test_guia_completa_nao_recebe_nada():
    no_portal = ["ENTREGA_a.jpg", "LAUDO_PANORAMICA_1_OFICIAL.pdf", "imagemGTO"]
    disponiveis = ["LAUDO_PANORAMICA_1_OFICIAL.pdf", "ENTREGA_b.jpg"]
    assert arquivos_que_faltam({"panoramica"}, no_portal, disponiveis) == []


def test_imagem_sobe_quando_a_guia_nao_tem_nenhuma():
    no_portal = ["LAUDO_PANORAMICA_1_OFICIAL.pdf", "imagemGTO"]
    disponiveis = ["ENTREGA_35e0b38da0.jpg", "ENTREGA_774bbea8dd.jpg"]
    r = arquivos_que_faltam({"panoramica"}, no_portal, disponiveis)
    assert r == ["ENTREGA_35e0b38da0.jpg", "ENTREGA_774bbea8dd.jpg"]


def test_imagem_nao_sobe_se_a_guia_ja_tem_imagem():
    """Folha composta: uma imagem pode cobrir varios exames. Sem saber o que tem
    DENTRO dela, acrescentar outra e arriscar duplicar — nao sobe."""
    no_portal = ["image - 2026-09-01T115649.100.jpg", "LAUDO_PANORAMICA_1_OFICIAL.pdf"]
    disponiveis = ["ENTREGA_x.jpg"]
    assert arquivos_que_faltam({"panoramica"}, no_portal, disponiveis) == []


def test_arquivo_de_exame_irreconhecivel_nao_sobe():
    """Na duvida sobre QUAL exame o arquivo cobre, nao anexa: o portal nao remove."""
    no_portal = ["ENTREGA_a.jpg", "imagemGTO"]
    disponiveis = ["documento_qualquer.pdf"]
    assert arquivos_que_faltam({"panoramica"}, no_portal, disponiveis) == []


def test_solicitacao_nunca_entra_no_completar():
    """Completar cobre laudo e imagem. Pedido do dentista e outra conversa — quem
    decide se ele serve e o gate da esteira, com leitura do documento."""
    no_portal = ["ENTREGA_a.jpg", "imagemGTO"]
    disponiveis = ["SOLICITACAO_0__FULANO.jpg", "LAUDO_PANORAMICA_1_OFICIAL.pdf"]
    r = arquivos_que_faltam({"panoramica"}, no_portal, disponiveis)
    assert r == ["LAUDO_PANORAMICA_1_OFICIAL.pdf"]
