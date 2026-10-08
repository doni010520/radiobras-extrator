"""Falha de consulta ao PRORADIS nao pode virar "nao encontrado" nem "cobrar laudo".

Frente C (08/10): a consulta da worklist engolia a excecao e devolvia lista vazia;
vazia virava SEM_MATCH ("o nome esta escrito diferente", DIEGO 197270606 em 15/09)
ou SEM_ARQUIVOS ("cobrar a emissao do laudo com o radiologista", lotes inteiros com
zero arquivo em 1-8 s: execs 1108, 1585; 10 min depois a 1586 baixou as 5). As duas
mandam a operadora atras da pessoa errada. Agora a falha e REGISTRADA e, sem nada
achado, a guia vira ERRO (falha tecnica nossa, retry).
"""
import esteira
import extrator_arquivos as ea


class _PageQuebrada:
    def evaluate(self, *a):
        raise RuntimeError("Execution context was destroyed")

    def wait_for_timeout(self, ms):
        pass


def test_worklist_registra_a_falha_em_vez_de_so_devolver_vazio():
    falhas = []
    assert ea.listar_worklist_por_pacientes(_PageQuebrada(), "15/09/2026",
                                            ["DIEGO DE JESUS ALBANO"], falhas=falhas) == []
    assert falhas, "a falha tem que ficar registrada"


def test_sem_o_parametro_o_comportamento_antigo_continua():
    assert ea.listar_worklist_por_pacientes(_PageQuebrada(), "15/09/2026", ["X Y"]) == []


def test_zero_arquivo_por_falha_de_consulta_e_erro_nosso():
    st, det = esteira._status_download({"falhas_consulta": ["worklist: timeout"]}, 0)
    assert st == "ERRO" and "falha técnica" in det


def test_zero_arquivo_por_excecao_no_laudo_e_erro_nosso():
    st, det = esteira._status_download(
        {"pendencias": ["erro laudos: Target page, context or browser has been closed"]}, 0)
    assert st == "ERRO" and "falha técnica" in det


def test_zero_arquivo_sem_falha_continua_sem_arquivos():
    """Exame registrado e laudo ainda nao emitido: ai sim e cobrar o radiologista."""
    st, _ = esteira._status_download({"pendencias": ["nenhum laudo disponivel"]}, 0)
    assert st == "SEM_ARQUIVOS"


def test_com_arquivo_a_falha_parcial_nao_derruba():
    st, _ = esteira._status_download({"falhas_consulta": ["worklist: timeout"]}, 3)
    assert st == "BAIXADO"
