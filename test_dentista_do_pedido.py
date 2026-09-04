"""O pedido tem de ser do DENTISTA da guia. Regra do dono (29/08): "comparar o
dentista e obrigatorio e basico".

Caso INGRID EMILE DE SOUZA (GTO 196333450, exame 18/08, Centro): o prontuario
tinha 9 anexos e 7 candidatos a solicitacao. Nao havia pedido do atendimento de
2026, entao o robo desceu para o mais recente que existia — 07/10/2025, de outro
episodio e outro dentista — reconheceu que estava vencido e REESCREVEU a data por
cima da imagem antes de anexar. A operacao viu no portal: "faturando com nome de
dentista solicitante diferente".

O `_dentista_contradiz` ja existia e e conservador (so acusa com 2+ tokens
legiveis, zero em comum e sem CRO batendo), mas so era consultado nos ramos de
FALLBACK — quando o nome do paciente nao era lido. No caminho feliz (nome bate,
exames cobrem) o dentista nunca era olhado. Era exatamente o caso da INGRID."""
from esteira import _escolher_solicitacao


def _folha(idx, paciente, exames, dentista="", cro_lido="", data="18/08/2026"):
    return {"idx": idx, "tipo": "solicitacao", "legivel": True,
            "paciente_lido": paciente, "exames_lidos": exames, "texto": "",
            "data_solicitacao": data, "dentista_lido": dentista,
            "cro_lido": cro_lido, "arquivo_origem": "SOLIC_20260818.pdf"}


def test_pedido_de_outro_dentista_nao_fatura_mesmo_com_nome_e_exames_batendo():
    leituras = [_folha(0, "INGRID EMILE DE SOUZA", ["panoramica", "periapical"],
                       dentista="ROBERTO CARLOS ALVES PEREIRA")]
    idx, a, motivo = _escolher_solicitacao(
        leituras, "INGRID EMILE DE SOUZA", {"panoramica", "periapical"}, 1,
        dentista_gto="FERNANDA KEURY SILVA ROCHA")
    assert idx is None, "anexou pedido assinado por outro dentista"
    assert motivo == "OUTRO_DENTISTA"


def test_mesmo_dentista_continua_faturando():
    """Controle: a trava nao pode derrubar o caminho normal."""
    leituras = [_folha(0, "INGRID EMILE DE SOUZA", ["panoramica", "periapical"],
                       dentista="FERNANDA KEURY SILVA ROCHA")]
    idx, a, motivo = _escolher_solicitacao(
        leituras, "INGRID EMILE DE SOUZA", {"panoramica", "periapical"}, 1,
        dentista_gto="FERNANDA KEURY SILVA ROCHA")
    assert idx == 0 and motivo is None


def test_dentista_ilegivel_nao_barra():
    """Conservador de proposito: letra manuscrita falha o tempo todo. Sem leitura
    (ou com 1 token so) nao ha contradicao — a guia segue pelo caminho normal."""
    leituras = [_folha(0, "INGRID EMILE DE SOUZA", ["panoramica", "periapical"],
                       dentista="")]
    idx, a, motivo = _escolher_solicitacao(
        leituras, "INGRID EMILE DE SOUZA", {"panoramica", "periapical"}, 1,
        dentista_gto="FERNANDA KEURY SILVA ROCHA")
    assert idx == 0 and motivo is None


def test_sobrenome_em_comum_nao_e_contradicao():
    """Familia de dentistas / leitura parcial: 1 token em comum ja desarma."""
    leituras = [_folha(0, "INGRID EMILE DE SOUZA", ["panoramica", "periapical"],
                       dentista="MYLENA KEURY SILVA")]
    idx, a, motivo = _escolher_solicitacao(
        leituras, "INGRID EMILE DE SOUZA", {"panoramica", "periapical"}, 1,
        dentista_gto="FERNANDA KEURY SILVA ROCHA")
    assert idx == 0 and motivo is None


def test_registra_OS_NOMES_dos_dois_dentistas():
    """A mensagem dizia "assinado por OUTRO dentista" e mandava conferir — sem dizer
    QUEM contra QUEM. Para saber, a pessoa tinha de abrir a guia e abrir o documento.

    Em 04/09 quatro guias cairam aqui (GABRIEL DA SILVA, TATIANA DO AMPARO, MARCIO
    RIBEIRO e TALITA SOUZA) e nao havia como responder "qual era o dentista errado?"
    — os dois nomes eram usados na comparacao e descartados. Sem eles nao da para
    saber se e a mesma dentista em todos os casos (alguem que saiu da clinica e cujos
    pedidos seguem no prontuario) ou situacoes independentes."""
    det = {}
    leituras = [_folha(0, "INGRID EMILE DE SOUZA", ["panoramica", "periapical"],
                       dentista="ROBERTO CARLOS ALVES PEREIRA")]
    idx, a, motivo = _escolher_solicitacao(
        leituras, "INGRID EMILE DE SOUZA", {"panoramica", "periapical"}, 1,
        dentista_gto="FERNANDA KEURY SILVA ROCHA", detalhe=det)
    assert motivo == "OUTRO_DENTISTA"
    assert det.get("dentista_gto") == "FERNANDA KEURY SILVA ROCHA"
    assert det.get("dentista_lido") == "ROBERTO CARLOS ALVES PEREIRA"


def test_nao_suja_o_detalhe_quando_o_dentista_bate():
    det = {}
    leituras = [_folha(0, "INGRID EMILE DE SOUZA", ["panoramica", "periapical"],
                       dentista="FERNANDA KEURY SILVA ROCHA")]
    _escolher_solicitacao(leituras, "INGRID EMILE DE SOUZA",
                          {"panoramica", "periapical"}, 1,
                          dentista_gto="FERNANDA KEURY SILVA ROCHA", detalhe=det)
    assert "dentista_lido" not in det
