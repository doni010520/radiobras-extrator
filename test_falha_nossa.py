"""Regra do dono (22/08/26): FALHA DE SISTEMA NAO E PENDENCIA DO PAINEL.

Toda falha NOSSA (tecnica) some da tela da RadioBras, entra no loop de retry
(try again, mesmo teto do transitorio) e e notificada ao dono por WhatsApp.
O que e DELES (clinica/radiologista/cadastro) e o que precisa de OLHO HUMANO no
documento (Conferencia) continua no painel — a operacao nao perde trabalho real."""
from db import eh_nosso, eh_pendencia_front

_NOME_NAO_BATE = "nenhum documento do prontuário está no nome deste paciente"
_GUIA_ILEGIVEL = "o robô não conseguiu ler quais exames a guia autoriza"
_ANEXACAO = "a anexação falhou no envio ao portal"
_GEMINI = "gemini: 503 UNAVAILABLE"
_NOSSAS = (_NOME_NAO_BATE, _GUIA_ILEGIVEL, _ANEXACAO, _GEMINI)


def test_as_quatro_falhas_nossas_sao_nossas():
    for m in _NOSSAS:
        assert eh_nosso(m) is True, m


def test_categoria_erro_e_nossa_mesmo_sem_texto_conhecido():
    # _decidir falhou (Gemini/anexos): a esteira marca categoria='erro'. O texto pode
    # nao casar regex nenhuma e cair em 'outros'; a categoria sozinha ja prova que a
    # falha e nossa — era o furo apontado no design de 02/08.
    assert eh_nosso("motivo qualquer sem padrão", "erro") is True


def test_o_que_e_deles_nao_e_nosso():
    for m in ("falta o LAUDO do radiologista",
              "não há nenhum pedido do dentista anexado ao prontuário",
              "o pedido do dentista não cobre tudo que a guia autoriza",
              "paciente não foi encontrado no PRORADIS"):
        assert eh_nosso(m) is False, m


def test_conferencia_continua_no_painel():
    # Olho humano no documento NAO e falha tecnica — esconder seria sumir com
    # trabalho real da operacao.
    for m in ("a caligrafia do pedido está ilegível",
              "há mais de um paciente com esse nome no PRORADIS no mesmo dia",
              "revisão humana"):
        assert eh_nosso(m) is False, m
        assert eh_pendencia_front(m, "") is True, m


def test_falha_nossa_nunca_aparece_no_painel_nem_esgotada():
    # Antes: esgotar o retry devolvia a falha nossa pro operador como "Investigar".
    # Agora ela NUNCA volta pro painel — esgotou, o dono e avisado no WhatsApp.
    for m in _NOSSAS:
        assert eh_pendencia_front(m, "", 0) is False, m
        assert eh_pendencia_front(m, "", 99) is False, m


def test_falha_nossa_entra_no_loop_de_retry():
    from db import deve_entrar_no_retry
    for m in _NOSSAS:
        assert deve_entrar_no_retry(m, "") is True, m


def test_externo_e_conferencia_nunca_entram_no_retry():
    from db import deve_entrar_no_retry
    # re-tentar nao faz laudo aparecer nem pedido ser anexado pela clinica
    assert deve_entrar_no_retry("falta o LAUDO do radiologista", "") is False
    assert deve_entrar_no_retry("não há nenhum pedido do dentista", "") is False
    # conferencia precisa de humano: retry cego so gasta quota do Gemini
    assert deve_entrar_no_retry("a caligrafia do pedido está ilegível", "") is False
