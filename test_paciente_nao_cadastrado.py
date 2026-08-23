"""'Paciente nao encontrado no cadastro' nao e falha tecnica nossa.

Caso MARIA DE FATIMA LAMOEDO (196370003, 388336, 19/08), visto na rodada #660.
Depois do conserto da busca, a mensagem virou honesta:

    paciente 'MARIA DE FATIMA LAMOEDO' nao encontrado no cadastro do PRORADIS —
    conferir se o nome esta escrito igual nos dois sistemas

Mas a guia continuou escondida do painel e presa no retry, porque `eh_nosso` faz
curto-circuito em `categoria == 'erro'` ANTES de consultar o classificador (db.py:1267).

O atalho tem razao de existir: quando `_decidir` quebra por Gemini/anexos, o texto
pode nao casar regex nenhuma e cair em 'outros' — a categoria sozinha ja prova a
culpa (furo apontado no desenho de 02/08). So que ele passa por cima de um texto que
identifica a causa com todas as letras.

E a distincao importa pela regra do dono: falha de sistema se resolve por NOVA
TENTATIVA nossa. Aqui nenhuma re-tentativa faz o paciente aparecer — ele esta
cadastrado com outro nome (foi o caso do VALDEMIR, que o PRORADIS traz como
'VALDEMIR DOS SANTOS PEREIRA' e a guia chama de 'DOS ANJOS'). Quem resolve e o
cadastro. Ficar no retry e queimar 6 tentativas contra algo que nao muda sozinho.

Homonimo continua NOSSO de proposito: ali o re-run desempata pelo nascimento
(caso ALESSANDRA)."""
import db


_NAO_CADASTRADO = ("anexos: paciente 'MARIA DE FATIMA LAMOEDO' não encontrado no "
                   "cadastro do PRORADIS — conferir se o nome está escrito igual "
                   "nos dois sistemas")
_HOMONIMO = ("anexos: 2 paciente(s) com o nome 'ALESSANDRA' no PRORADIS — não foi "
             "possível identificar o prontuário com segurança")
_GEMINI = ("NÃO FATUROU por falha técnica na leitura dos documentos. "
           "Detalhe: gemini: 400 INVALID_ARGUMENT")


# ── o caso ────────────────────────────────────────────────────────────────
def test_nao_cadastrado_nao_e_nosso_mesmo_com_categoria_erro():
    assert db.eh_nosso(_NAO_CADASTRADO, "erro") is False


def test_nao_cadastrado_aparece_no_painel():
    assert db.eh_pendencia_front(_NAO_CADASTRADO, "erro") is True


def test_nao_cadastrado_nao_entra_no_retry():
    """Re-tentar nao faz o paciente aparecer — ele esta com outro nome."""
    assert db.deve_entrar_no_retry(_NAO_CADASTRADO, "erro") is False


def test_nao_cadastrado_vai_para_o_CADASTRO():
    chave, quem, acao = db.classificar_pendencia(_NAO_CADASTRADO, "erro")
    assert chave == "paciente_nao_achado"
    assert quem == "Cadastro"


# ── o atalho de 'erro' continua valendo para o resto ───────────────────────
def test_falha_do_gemini_continua_NOSSA():
    """O motivo do atalho existir: quebra tecnica que nao casa regex nenhuma."""
    assert db.eh_nosso(_GEMINI, "erro") is True
    assert db.eh_pendencia_front(_GEMINI, "erro") is False


def test_texto_generico_com_categoria_erro_continua_NOSSO():
    assert db.eh_nosso("qualquer coisa que ninguem previu", "erro") is True


def test_homonimo_continua_NOSSO_e_no_retry():
    """Caso ALESSANDRA: com 2+ cards o nascimento desempata num re-run — ali
    insistir FUNCIONA, entao continua sendo nosso."""
    assert db.eh_nosso(_HOMONIMO, "erro") is True
    assert db.deve_entrar_no_retry(_HOMONIMO, "erro") is True


def test_sem_categoria_erro_nada_muda():
    assert db.eh_nosso(_NAO_CADASTRADO, "sem_solicitacao") is False
