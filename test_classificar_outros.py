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
