"""JUNIOR / FILHO / NETO nao sao sobrenome — sao marcador de geracao.

E a trava vale no portao do DOCUMENTO, NAO no match do PACIENTE. Esta segunda frase
custou uma regressao em producao, no mesmo dia: vale a pena contar direito.

O QUE A TRAVA PROTEGE. A guia 196348961 e de HELIO DE SOUZA OLIVEIRA; o PRORADIS
cadastra 'HELIO DE SOUZA OLIVEIRA JUNIOR'. Se um pedido em nome do JUNIOR for aceito
como sendo do pai (ou vice-versa), anexamos documento de terceiro — a familia
JOCASTA, com o agravante de as duas pessoas dividirem o nome INTEIRO.

ONDE ELA NAO PODE FICAR. O veto nasceu dentro de `_nomes_compat`, que parecia o lugar
natural. Nao era: `_nomes_compat` tambem governa `_casam_por_paciente`, o match do
PACIENTE na worklist do PRORADIS, no estagio de DOWNLOAD. La um "nao bate" nao quer
dizer "documento de outra pessoa" — quer dizer "o paciente nao existe".

O estrago, medido no banco, mesma guia, mesmo dia:

    exec 620 / 636 / 656   sem_laudo   n_arquivos=2   "[DL6] BAIXADO"
    --- deploy do veto dentro de _nomes_compat ---
    exec 665               sem_exame   n_arquivos=0   "SEM_MATCH"

De 'falta_laudo / Radiologista / o robo anexa sozinho quando o laudo sair' para
'paciente_nao_achado / Cadastro'. E beco sem saida: fora do retry, sem botao de
confirmar, e reprocessar depois que o laudo saisse daria SEM_MATCH de novo, para
sempre.

A medicao que autorizou a mudanca ("4135 faturados, ZERO afetados") estava errada:
olhou so guias faturadas, comparando com o nome lido no DOCUMENTO. Nao cobria as
pendencias abertas (onde o HELIO estava) nem o match da worklist. Varrendo os 7317
execucao_itens aparecem 5 divergencias, 2 em guias FATURADAS — a GTO 195540484
(CARLOS ALBERTO CARVALHO DA SILVA JUNIOR, pedido lido sem o 'JUNIOR') passaria a
falhar."""
from esteira import _mesma_geracao, _nomes_compat


# ── a trava, no lugar certo: o DOCUMENTO ──────────────────────────────────
def test_junior_nao_e_o_pai():
    assert _mesma_geracao("HELIO DE SOUZA OLIVEIRA JUNIOR",
                          "HELIO DE SOUZA OLIVEIRA") is False


def test_pai_nao_e_o_junior():
    """Nos dois sentidos — o lado que traz o marcador nao importa."""
    assert _mesma_geracao("HELIO DE SOUZA OLIVEIRA",
                          "HELIO DE SOUZA OLIVEIRA JUNIOR") is False


def test_filho_neto_sobrinho_tambem():
    for marca in ("FILHO", "NETO", "SOBRINHO", "JR"):
        assert _mesma_geracao(f"JOSE DA SILVA {marca}", "JOSE DA SILVA") is False, marca


def test_marcador_nos_DOIS_lados_e_a_mesma_geracao():
    assert _mesma_geracao("GILDASIO DOS SANTOS OLIVEIRA SOBRINHO",
                          "GILDASIO DOS SANTOS OLIVEIRA SOBRINHO") is True


def test_sem_marcador_nenhum_e_a_mesma_geracao():
    assert _mesma_geracao("PRISCILA F. S. DANTAS",
                          "PRISCILA FARIAS DOS SANTOS DANTAS") is True


def test_acento_e_caixa_nao_atrapalham():
    assert _mesma_geracao("Jose da Silva Junior", "JOSÉ DA SILVA JUNIOR") is True


# ── e NAO no match do PACIENTE ────────────────────────────────────────────
def test_nomes_compat_NAO_veta_geracao():
    """A regressao. _nomes_compat governa o match do paciente na worklist; vetar ali
    faz o robo dizer que o paciente NAO EXISTE, e a guia vira beco sem saida."""
    assert _nomes_compat("HELIO DE SOUZA OLIVEIRA JUNIOR",
                         "HELIO DE SOUZA OLIVEIRA") is True


def test_guia_faturada_com_JUNIOR_continua_casando():
    """GTO 195540484, faturada em 29/07: o pedido foi lido sem o 'JUNIOR'. Com o
    veto dentro de _nomes_compat ela passaria a falhar."""
    assert _nomes_compat("Carlos Alberto Carvalho da Silva",
                         "CARLOS ALBERTO CARVALHO DA SILVA JUNIOR") is True


# ── o resto do matcher intacto ────────────────────────────────────────────
def test_irmao_com_nome_inteiro_diferente():
    assert _nomes_compat("Pedro Silva Santos", "JOAO SILVA SANTOS") is False


def test_documento_de_outra_pessoa_continua_recusado():
    assert _nomes_compat("GLADYS FREITAS DOS SANTOS",
                         "HOSANA BARRETO DOS SANTOS") is False


def test_iniciais_continuam_valendo():
    assert _nomes_compat("Priscila F. S. Dantas",
                         "PRISCILA FARIAS DOS SANTOS DANTAS") is True


def test_erro_de_grafia_continua():
    assert _nomes_compat("Sophia Carvallo do Rosamo",
                         "SOPHIA CARVALHO DO ROSARIO") is True
