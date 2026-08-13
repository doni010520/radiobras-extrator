"""Os 3 motivos que caíam em 'Outros' (fallback sem ação) na reconciliação 13/08 —
agora têm chave, responsável e ação próprios. Todos são Conferência (a conferir)."""
from db import classificar_pendencia


def test_solicitacao_nao_confirmada_nao_e_outros():
    m = ("NÃO FATUROU porque a solicitação do dentista não pôde ser confirmada para "
         "esta guia — há documento no nome do paciente no prontuário, mas a "
         "solicitação encontrada está mal-lida/ilegível ou pede exame diferente.")
    chave, quem, acao = classificar_pendencia(m, "sem_solicitacao")
    assert chave != "outros"
    assert quem == "Conferência"
    assert "prontuário" in acao or "anexar" in acao


def test_revisao_humana_nao_e_outros():
    chave, quem, acao = classificar_pendencia("revisão humana", "auto")
    assert chave != "outros"
    assert quem == "Conferência"


def test_data_ajustada_nao_e_outros():
    chave, quem, acao = classificar_pendencia("Data ajustada automaticamente.", "auto")
    assert chave != "outros"
    assert quem == "Conferência"


def test_motivo_da_tele_classifica_esperando_tele():
    # O motivo que salvar_execucao gera p/ doc-orto-sem-traçado (⚠️#2, 13/08) TEM que
    # cair em 'esperando_tele' (Radiologista) — não no 'auto/faturaria' de antes, nem
    # no 'falta_laudo' genérico. Guarda o acoplamento texto↔classificador.
    m = ("Documentação ortodôntica com a panorâmica anexada, mas SEM o LAUDO da "
         "telerradiografia (traçado cefalométrico). O robô anexa sozinho assim que o "
         "traçado sair no PRORADIS — cobrar a emissão do traçado.")
    chave, quem, acao = classificar_pendencia(m, "sem_laudo")
    assert chave == "esperando_tele"
    assert quem == "Radiologista"
