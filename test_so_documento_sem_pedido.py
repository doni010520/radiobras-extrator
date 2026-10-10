"""Prontuario so com documento de identidade, nenhum pedido (caso NICOLAS 198275881).

A mensagem dizia "solicitacao mal-lida/ilegivel ou pede exame diferente" e mandava
procurar um pedido que nao existe. E falta da clinica: "sem pedido"."""
import esteira


def test_mensagem_de_sem_pedido_quando_so_ha_documento(monkeypatch):
    import db
    m = ("NÃO FATUROU porque o prontuário não tem nenhum pedido do dentista: há "
         "documento de identidade no nome do paciente, mas nenhum anexo é pedido de exame.")
    chave, quem, _ = db.classificar_pendencia(m, "sem_solicitacao")
    assert (chave, quem) == ("sem_pedido", "Clínica")


def test_ha_leitura_no_nome_com_so_documento():
    lei = [{"idx": 0, "tipo": "documento", "paciente_lido": "NICOLAS DOS SANTOS NASCIMENTO RIBEIRO"}]
    assert esteira._ha_leitura_no_nome(lei, "NICOLAS DOS SANTOS NASCIMENTO RIBEIRO")
    assert not any(l.get("tipo") == "solicitacao" for l in lei)
