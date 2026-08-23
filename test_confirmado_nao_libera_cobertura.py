"""O 'sinal verde humano' confirma a IDENTIDADE. Ele nao pode confirmar a COBERTURA.

Feature de 13/08: quando a operadora abre a pendencia, olha o pedido e clica em
"Confirmei que e o paciente", `nome_confirmado=True` libera a trava do NOME. Junto
veio uma segunda liberacao (esteira.py:682) com esta justificativa:

    "Confirmacao humana tambem libera a COBERTURA: na ilegivel os exames nem
     sempre sao lidos, entao 'cobre' seria falso a toa."

Para pedido ILEGIVEL isso esta certo — os exames nao foram lidos, `ex` sai vazio, e
exigir cobertura de um conjunto vazio reprovaria todo mundo.

Mas a liberacao foi escrita SEM condicao, e por isso vaza para o caso oposto: o robo
LEU os exames, eles NAO cobrem a guia, e mesmo assim um clique de identidade forca a
anexacao. Caso real LEDA MARIA MOREIRA DE CASTRO (GTO 196391551, 18/08): a guia pede
panoramica + periapical, o candidato escolhido e a folha do PERIAPICAL (lidos=
['periapical'], falta={'panoramica'}). Com nome_confirmado=True a funcao retorna ANTES
da uniao de folhas e anexa so a folha do periapical numa guia que autoriza as duas —
faturamento de exame que aquele papel nao autoriza. Anexacao e IRREVERSIVEL e isso e
glosa.

A distincao correta nao e "quem clicou", e "os exames foram lidos?":
  - exames NAO lidos (ilegivel)  -> a cobertura e indeterminada, o humano decide;
  - exames LIDOS e nao cobrem    -> o humano confirmou a PESSOA, nao o CONTEUDO.
"""
from esteira import _escolher_solicitacao


def _folha(idx, paciente, exames, texto="", cro="29369", data="18/08/2026"):
    return {"idx": idx, "tipo": "solicitacao", "legivel": True,
            "paciente_lido": paciente, "exames_lidos": exames, "texto": texto,
            "data_solicitacao": data, "cro": cro,
            "arquivo_origem": "SOLIC_20260818.pdf"}


# ── o vazamento (caso LEDA) ────────────────────────────────────────────────
def test_confirmar_identidade_nao_forca_pedido_que_nao_cobre():
    """Exames LIDOS e insuficientes: confirmar a pessoa nao pode liberar."""
    leituras = [_folha(0, "LEDA MARIA MOREIRA DE CASTRO", ["periapical"])]
    idx, a, motivo = _escolher_solicitacao(
        leituras, "LEDA MARIA MOREIRA DE CASTRO",
        {"panoramica", "periapical"}, 1, nome_confirmado=True)
    assert idx is None, "anexaria folha de periapical numa guia pan+peri"
    assert motivo == "NAO_COBRE"


def test_sem_confirmacao_o_comportamento_e_o_mesmo():
    """Controle: sem o clique tambem reprova — o clique nao pode ser a diferenca."""
    leituras = [_folha(0, "LEDA MARIA MOREIRA DE CASTRO", ["periapical"])]
    idx, a, motivo = _escolher_solicitacao(
        leituras, "LEDA MARIA MOREIRA DE CASTRO",
        {"panoramica", "periapical"}, 1, nome_confirmado=False)
    assert idx is None and motivo == "NAO_COBRE"


# ── o caso que a liberacao existe para atender: ILEGIVEL ───────────────────
def test_pedido_ilegivel_continua_liberado_pelo_humano():
    """Exames NAO lidos -> `ex` vazio -> 'cobre' seria falso a toa. Aqui a
    confirmacao humana E a unica informacao disponivel e tem de valer, senao a
    feature de 13/08 morre e o caso MARIA CLARA volta a travar."""
    leituras = [_folha(0, "nome ilegivel", [], texto="")]
    idx, a, motivo = _escolher_solicitacao(
        leituras, "PRISCILA FARIAS DOS SANTOS", {"periapical"}, 1,
        nome_confirmado=True)
    assert idx == 0, "pedido ilegivel confirmado a mao tem de passar"
    assert motivo is None


def test_confirmado_com_pedido_que_cobre_passa():
    """O caminho normal: cobre de verdade, o clique so resolveu o nome."""
    leituras = [_folha(0, "Coula Patricia hacial", ["periapical"])]
    idx, a, motivo = _escolher_solicitacao(
        leituras, "CARLA PATRICIA DA CRUZ MACIEL", {"periapical"}, 1,
        nome_confirmado=True)
    assert idx == 0 and motivo is None
