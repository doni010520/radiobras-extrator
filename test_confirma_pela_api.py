"""A conferencia do upload nao pode ler a MESMA tela onde os arquivos foram soltos.

Caso ANA PAULA VIEIRA DIAS (196933166, exame 02/09, rodada de 05/09 11:15): o robo
enviou 3 arquivos e o log disse "OK (3 enviados)". Chegou UM — o laudo. A folha de
imagem e a solicitacao sumiram, e a guia foi dada por faturada sem imagem, que e
exatamente a glosa 3230.

Por que passou: `_anexos_nomes` varre o body inteiro atras de nome de arquivo, e o
`input[type=file]` EXIBE os nomes que acabaram de ser selecionados. Esses chips
contem, por construcao, todos os `enviados` — entao a conferencia pelo DOM nunca
consegue acusar falta. E `_resolver_contagem` prefere o DOM sempre que o contador
da popup renderiza, mesmo tendo a API autoritativa na mao.

Regra: para CONFERIR uma escrita, a API manda. O DOM so vale se a API falhar, e nao
conseguir confirmar por nenhuma das duas NAO e sucesso.

Medido em 04-08/09: 4 guias perderam arquivo em silencio (LUCA 196831581, DAYANE
196811360, VINICIUS 196850844, ANA PAULA 196933166). Nas quatro o log trazia
"DOM nao leu os anexos" logo antes do envio."""
from extrator_odontoprev import _confirmar_anexos, _nao_grudaram


def _api(n, nomes):
    return lambda: (n, set(nomes))


def test_api_manda_quando_o_dom_mostra_os_chips_do_seletor():
    """O DOM 've' os 3 porque o proprio input exibe o que foi selecionado."""
    dom = {"LAUDO_PANORAMICA_40348164_OFICIAL.pdf", "ENTREGA_faeb5a8511.jpg",
           "SOLICITACAO_0__ana_paula_dias.jpg"}
    api = _api(2, {"imagemGTO", "LAUDO_PANORAMICA_40348164_OFICIAL.pdf"})
    nomes, fonte = _confirmar_anexos(api, dom)
    assert fonte == "API"
    assert nomes == {"imagemGTO", "LAUDO_PANORAMICA_40348164_OFICIAL.pdf"}


def test_caso_ana_paula_o_upload_parcial_e_acusado():
    """Fim a fim da regra: com a API, os dois que nao grudaram aparecem."""
    enviados = {"LAUDO_PANORAMICA_40348164_OFICIAL.pdf", "ENTREGA_faeb5a8511.jpg",
                "SOLICITACAO_0__ana_paula_dias.jpg"}
    nomes, _ = _confirmar_anexos(
        _api(2, {"imagemGTO", "LAUDO_PANORAMICA_40348164_OFICIAL.pdf"}),
        set(enviados))
    assert _nao_grudaram(nomes, enviados) == [
        "ENTREGA_faeb5a8511.jpg", "SOLICITACAO_0__ana_paula_dias.jpg"]


def test_sem_api_o_dom_ainda_serve():
    """API fora do ar nao pode travar o envio; o DOM volta a valer."""
    dom = {"imagemGTO", "LAUDO_X_1_OFICIAL.pdf"}
    nomes, fonte = _confirmar_anexos(_api(-1, set()), dom)
    assert fonte == "DOM"
    assert nomes == dom


def test_api_que_estoura_nao_derruba_a_conferencia():
    def explode():
        raise RuntimeError("token vencido")
    nomes, fonte = _confirmar_anexos(explode, {"imagemGTO"})
    assert fonte == "DOM"
    assert nomes == {"imagemGTO"}


def test_nenhuma_fonte_legivel_nao_e_sucesso():
    """'Nao consegui conferir' != 'grudou'. Quem chama tem que saber a diferenca."""
    nomes, fonte = _confirmar_anexos(_api(-1, set()), set())
    assert fonte == "nenhuma"
    assert nomes == set()


def test_api_vazia_e_resposta_valida_e_acusa_tudo():
    """Guia sem anexo nenhum na API: os enviados realmente nao estao la."""
    nomes, fonte = _confirmar_anexos(_api(0, set()), {"ENTREGA_a.jpg"})
    assert fonte == "API"
    assert nomes == set()
    assert _nao_grudaram(nomes, {"ENTREGA_a.jpg"}) == ["ENTREGA_a.jpg"]
