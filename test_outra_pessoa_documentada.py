"""Pedido e documento de identidade no nome de OUTRA pessoa (10/10).

Os casos "so o prenome difere" eram, na maioria, irmaos com os mesmos sobrenomes:
o prontuario tinha o RG/CTPS com o mesmo nome lido no pedido. A mensagem dizia
"provavelmente E deste paciente" e mandava conferir. Agora diz que e outra pessoa e
a cobranca vai para a clinica. NADA passa a ser aceito por isto."""
import db
import esteira


def _l(tipo, nome):
    return {"tipo": tipo, "paciente_lido": nome}


def test_bruno_pamela():
    lei = [_l("solicitacao", "Pamela De Oliveira Souza"), _l("documento", "Pamela de Oliveira Souza")]
    assert esteira._outra_pessoa_documentada(lei, "BRUNO DE OLIVEIRA SOUZA") == "Pamela De Oliveira Souza"


def test_lenilton_hamilton():
    lei = [_l("solicitacao", "hamilton Oliveira Santos"), _l("documento", "HAMILTON OLIVEIRA SANTOS")]
    assert esteira._outra_pessoa_documentada(lei, "LENILTON OLIVEIRA SANTOS")


def test_sem_documento_continua_caso_de_conferencia():
    """ANETE lida como 'Plunet': sem RG de 'Plunet', pode ser leitura ruim."""
    lei = [_l("solicitacao", "Plunet Andrade de Mattos")]
    assert esteira._outra_pessoa_documentada(lei, "ANETE ANDRADE DE MATTOS") == ""


def test_documento_do_proprio_paciente_nao_conta():
    lei = [_l("solicitacao", "Plunet Andrade de Mattos"), _l("documento", "ANETE ANDRADE DE MATTOS")]
    assert esteira._outra_pessoa_documentada(lei, "ANETE ANDRADE DE MATTOS") == ""


def test_pedido_do_paciente_nao_e_outra_pessoa():
    lei = [_l("solicitacao", "Bruno de Oliveira Souza"), _l("documento", "Pamela de Oliveira Souza")]
    assert esteira._outra_pessoa_documentada(lei, "BRUNO DE OLIVEIRA SOUZA") == ""


def test_mensagem_vai_para_a_clinica():
    m = ("NÃO FATUROU porque o pedido encontrado é de OUTRA PESSOA: está no nome de "
         "'Pamela De Oliveira Souza', e o prontuário tem um documento de identidade "
         "com esse mesmo nome — não é erro de leitura. Nenhum documento do prontuário "
         "está no nome deste paciente.")
    chave, quem, _ = db.classificar_pendencia(m, "sem_solicitacao")
    assert quem == "Clínica", (chave, quem)
