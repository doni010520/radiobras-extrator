"""O gate da analise cefalometrica precisa ENXERGAR o pedido do dentista.

Regra do dono: *"sobre USP + Ricketts a questao nao esta no Doc orto compl.. esta na
SOLICITACAO DO DENTISTA. e la que isso vai ser tratado como necessario ou nao."*

A funcao `_analises_faltando_no_plano` foi escrita seguindo essa regra — ela le o que
o PEDIDO nomeia e compara com as secoes do CEPH. Mas ela lia de dois campos que, no
caminho de SUCESSO, nao existem:

    _txt_pedido  = dec.get("exames_lidos")       <- mora em dec["decisao"], nao na raiz
    _txt_pedido += dec.get("solicitacao_texto")  <- NUNCA foi escrito em lugar nenhum

`solicitacao_texto` aparecia uma unica vez no repositorio inteiro: nesta leitura.
Nenhum ponto do codigo o gravava. E `exames_lidos` e montado dentro do dicionario da
decisao (`out["decisao"]`), enquanto o gate recebe o dicionario de FORA.

Resultado: `analises_pedidas("")` devolvia vazio e a funcao retornava "nao falta
nada" — SEMPRE. A trava existia, tinha teste proprio, e estava morta exatamente no
caminho em que a guia vai faturar. O caso JOSEANE (15/08), que motivou a funcao,
voltaria a acontecer sem ninguem perceber.

Nao afrouxa nada: pedido que nao nomeia analise continua nao exigindo nenhuma."""
import esteira


def _dec(exames_lidos=None, texto="", laudo="LAUDO_TELERRADIOGRAFIA_CEPH_1_OFICIAL.pdf"):
    """Dicionario como a esteira monta no caminho de SUCESSO: a decisao (com os
    exames lidos e as leituras) fica em ['decisao'], nao na raiz."""
    return {
        "decisao": {"indice_solicitacao": 0, "anexar": True,
                    "exames_lidos": exames_lidos or [],
                    "leituras": [{"idx": 0, "tipo": "solicitacao", "texto": texto}]},
        "plano_solicitacao": "SOLICITACAO_0__pedido.pdf",
        "plano_laudo_imgs": [laudo],
        "pasta_dl": ".",
    }


def _laudo_com(txt):
    return lambda _p: txt


# ── o furo ────────────────────────────────────────────────────────────────
def test_analise_nomeada_na_LISTA_e_vista(monkeypatch):
    """Caso JOSEANE: pedido pede Ricketts, o CEPH so traz a analise USP."""
    import solicitacao_utils as su
    monkeypatch.setattr(su, "texto_do_laudo_pdf",
                        _laudo_com("Analise USP " + "x" * 300))
    falta, erro = esteira._analises_faltando_no_plano(
        _dec(exames_lidos=["telerradiografia com analise de Ricketts"]))
    assert erro is False
    assert "ricketts" in {str(x).lower() for x in falta}


def test_analise_nomeada_so_no_TEXTO_LIVRE_e_vista(monkeypatch):
    """A lista curada da IA as vezes dropa a secao (o proprio codigo reconhece isso
    em _texto_pedido). O nome da analise costuma estar na transcricao literal."""
    import solicitacao_utils as su
    monkeypatch.setattr(su, "texto_do_laudo_pdf",
                        _laudo_com("Analise USP " + "x" * 300))
    falta, erro = esteira._analises_faltando_no_plano(
        _dec(exames_lidos=["telerradiografia"],
             texto="SOLICITACAO DE EXAMES Telerradiografia lateral - Analise de Ricketts"))
    assert erro is False
    assert "ricketts" in {str(x).lower() for x in falta}


# ── nao pode virar trava boba ─────────────────────────────────────────────
def test_pedido_que_nao_nomeia_analise_nao_exige_nada(monkeypatch):
    """Regra de projeto: a maioria dos pedidos diz so 'Telerradiografia'. Exigir por
    padrao seguraria faturamento correto."""
    import solicitacao_utils as su
    monkeypatch.setattr(su, "texto_do_laudo_pdf",
                        _laudo_com("Analise USP " + "x" * 300))
    falta, erro = esteira._analises_faltando_no_plano(
        _dec(exames_lidos=["telerradiografia"], texto="Solicito telerradiografia"))
    assert falta == set() and erro is False


def test_analise_pedida_e_presente_no_laudo_passa(monkeypatch):
    import solicitacao_utils as su
    monkeypatch.setattr(su, "texto_do_laudo_pdf",
                        _laudo_com("Analise de Ricketts " + "y" * 300))
    falta, erro = esteira._analises_faltando_no_plano(
        _dec(exames_lidos=["telerradiografia com analise de Ricketts"]))
    assert falta == set() and erro is False


def test_laudo_ilegivel_e_falha_NOSSA_nao_do_radiologista(monkeypatch):
    """Se nao conseguimos abrir o PDF, dizer 'falta a analise' seria cobrar do
    radiologista um laudo que ele emitiu."""
    import solicitacao_utils as su
    monkeypatch.setattr(su, "texto_do_laudo_pdf", _laudo_com(""))
    falta, erro = esteira._analises_faltando_no_plano(
        _dec(exames_lidos=["telerradiografia com analise de Ricketts"]))
    assert erro is True and falta == set()


def test_sem_laudo_de_tele_quem_segura_e_outro_gate(monkeypatch):
    import solicitacao_utils as su
    monkeypatch.setattr(su, "texto_do_laudo_pdf", _laudo_com("qualquer coisa"))
    falta, erro = esteira._analises_faltando_no_plano(
        _dec(exames_lidos=["telerradiografia com analise de Ricketts"],
             laudo="LAUDO_PANORAMICA_1_OFICIAL.pdf"))
    assert falta == set() and erro is False


def test_formato_antigo_na_raiz_continua_funcionando(monkeypatch):
    """Compatibilidade: se algum caminho ainda puser exames_lidos na raiz, vale."""
    import solicitacao_utils as su
    monkeypatch.setattr(su, "texto_do_laudo_pdf",
                        _laudo_com("Analise USP " + "x" * 300))
    d = _dec()
    d["exames_lidos"] = ["telerradiografia com analise de Ricketts"]
    falta, erro = esteira._analises_faltando_no_plano(d)
    assert "ricketts" in {str(x).lower() for x in falta}
