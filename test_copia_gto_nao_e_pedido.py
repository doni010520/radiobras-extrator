"""Copia da GTO no prontuario nao vira pedido do dentista (JAQUELLINE, 10/10)."""
from esteira import _e_copia_de_gto, _rebaixar_copias_de_gto

# leituras REAIS do leitor (10/10) para a imagem da GTO
JAQUELLINE = {"idx": 0, "tipo": "solicitacao", "paciente_lido": "JAQUELLINE BATISTA DA SILVEIRA",
              "dentista_lido": "RAIANE DE SOUZA DANTAS", "cro_lido": "397950",
              "exames_lidos": ["panoramica", "periapical"],
              "texto": "RADIOGRAFIAS RADIOLOGIA ODONTOLOGICA DIGITAL\nRADIOGRAFIAS RADIOLOGIA "
                       "ODONTOLOGICA DIGITAL\nPanorâmica\nLev. Periapical"}
MERCIENE = {"idx": 1, "tipo": "solicitacao", "paciente_lido": "MERCIENE FELIX BRAGA",
            "dentista_lido": "ROSENIA CHAGAS FORTES CASTRO", "cro_lido": "410923",
            "exames_lidos": ["periapical"],
            "texto": "RADIOGRAFIAS RADIOLOGIA ODONTOLOGICA DIGITAL\nLev. Periapical"}
PEDIDO = {"idx": 2, "tipo": "solicitacao", "paciente_lido": "Taise Sousa Pereira",
          "dentista_lido": "Roseana Fortes", "cro_lido": "9752",
          "exames_lidos": ["panoramica", "telerradiografia"],
          "texto": "SOLICITAÇÃO DE EXAMES\nSolicito para: Taise Sousa Pereira\n"
                   "1. Rx Panorâmico em topo\n2. Telerradiografia Rickets"}


def test_gto_lida_como_solicitacao_e_reconhecida():
    assert _e_copia_de_gto(JAQUELLINE) and _e_copia_de_gto(MERCIENE)


def test_pedido_de_verdade_nao_e_gto():
    assert not _e_copia_de_gto(PEDIDO)


def test_bloco_de_pedido_da_radiologia_sozinho_nao_basta():
    """Pedido escrito no bloco impresso da propria radiologia: so o cabecalho nao
    derruba (precisa de 2 sinais)."""
    p = dict(PEDIDO, texto="RADIOGRAFIAS RADIOLOGIA ODONTOLOGICA DIGITAL\nSolicito panorâmica")
    assert not _e_copia_de_gto(p)


def test_rebaixa_so_a_copia():
    ls = [dict(JAQUELLINE), dict(PEDIDO)]
    out = {}
    assert _rebaixar_copias_de_gto(ls, out) == 1
    assert ls[0]["tipo"] == "outro" and ls[1]["tipo"] == "solicitacao"
    assert out["copias_gto_ignoradas"] == 1
