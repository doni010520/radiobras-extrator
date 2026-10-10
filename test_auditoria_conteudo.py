"""Veredito da auditoria de conteudo (10/10)."""
from auditoria_conteudo import veredito

GTO = {"idx": 0, "tipo": "gto", "paciente": "MARIA DA SILVA SOUZA",
       "profissional_solicitante": "ROSEANA FORTES", "conselho_numero": "9752",
       "exames": ["panoramica"]}


def _ped(nome="Maria da Silva Souza", dent="Roseana Fortes", cro="9752"):
    return {"idx": 1, "tipo": "pedido", "paciente": nome, "dentista": dent, "cro": cro,
            "exames": ["panoramica"]}


def _laudo(nome="MARIA DA SILVA SOUZA"):
    return {"idx": 2, "tipo": "laudo", "paciente": nome, "exames": ["radiografia panoramica"]}


IMG = {"idx": 3, "tipo": "imagem", "paciente": "MARIA DA SILVA SOUZA", "exames": ["panoramica"]}


def test_tudo_certo():
    assert veredito("MARIA DA SILVA SOUZA", "panoramica", [GTO, _ped(), _laudo(), IMG]) == ("OK", [])


def test_laudo_de_outra_pessoa_e_errado():
    s, m = veredito("MARIA DA SILVA SOUZA", "panoramica",
                    [GTO, _ped(), _laudo("JOANA PEREIRA LIMA"), IMG])
    assert s == "ERRADO" and "OUTRA pessoa" in m[0]


def test_pedido_de_outro_dentista_e_errado():
    s, m = veredito("MARIA DA SILVA SOUZA", "panoramica",
                    [GTO, _ped(dent="Carlos Alberto Nunes", cro="1111"), _laudo(), IMG])
    assert s == "ERRADO" and "OUTRO dentista" in m[0]


def test_sem_pedido_e_falta():
    s, m = veredito("MARIA DA SILVA SOUZA", "panoramica", [GTO, _laudo(), IMG])
    assert s == "FALTA" and "nenhum pedido" in m[0]


def test_sem_laudo_e_falta():
    s, _ = veredito("MARIA DA SILVA SOUZA", "panoramica", [GTO, _ped(), IMG])
    assert s == "FALTA"


def test_sem_imagem_e_incerto():
    s, _ = veredito("MARIA DA SILVA SOUZA", "panoramica", [GTO, _ped(), _laudo()])
    assert s == "INCERTO"


def test_exames_em_texto_nao_quebra():
    lau = {"idx": 2, "tipo": "laudo", "paciente": "MARIA DA SILVA SOUZA",
           "exames": "Radiografia Panorâmica"}
    assert veredito("MARIA DA SILVA SOUZA", "panoramica", [GTO, _ped(), lau, IMG])[0] == "OK"


def test_justificativa_dispensa_pedido_se_campo_49_preenchido():
    g = dict(GTO, campo_49="Paciente em tratamento ortodôntico")
    assert veredito("MARIA DA SILVA SOUZA", "panoramica", [g, _laudo(), IMG],
                    "justificativa")[0] == "OK"
    assert veredito("MARIA DA SILVA SOUZA", "panoramica", [GTO, _laudo(), IMG],
                    "justificativa")[0] == "INCERTO"


def test_abreviacao_no_papel_nao_e_outra_pessoa():
    for nome, papel in [("JURANDI DA SILVA BORGES JUNIOR", "Jurandi da Silva Borges Jr"),
                        ("MICHELLE DOS SANTOS PEREIRA", "Michele dos Stos Pereira"),
                        ("GIRLANE GONCALVES DE JESUS", "GIRLANE G. JESUS")]:
        p = _ped(nome=papel)
        assert veredito(nome, "panoramica", [GTO, p, _laudo(nome), dict(IMG, paciente=nome)])[0] == "OK", papel


def test_irmao_continua_outra_pessoa():
    s, _ = veredito("MARIA DA SILVA SOUZA", "panoramica",
                    [GTO, _ped(), _laudo("PEDRO DA SILVA SOUZA"), IMG])
    assert s == "ERRADO"


def test_campo17_lido_como_parente_nao_acusa_dentista():
    g = dict(GTO, profissional_solicitante="ALAN SANTOS PEREIRA", conselho_numero="10416")
    s, _ = veredito("SAMUEL TRINDADE PEREIRA", "panoramica",
                    [g, _ped(nome="Samuel Trindade Pereira", dent="Bruna Bomfim S. Dantas",
                             cro="38836"), _laudo("SAMUEL TRINDADE PEREIRA"),
                     dict(IMG, paciente="SAMUEL TRINDADE PEREIRA")])
    assert s == "OK"
