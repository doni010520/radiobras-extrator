"""Causa conhecida nao pode ser embrulhada em "falha tecnica na leitura".

Caso MARIA DE FATIMA LAMOEDO (196370003), rodada #669. Depois de dois consertos ela
AINDA saia como falha nossa, presa no retry e escondida do painel — mesmo com a causa
escrita por extenso dentro do proprio texto:

    NAO FATUROU por falha tecnica na leitura dos documentos — nao e problema do
    documento nem da clinica. O QUE FAZER: reprocessar o dia.
    Detalhe: anexos: paciente 'MARIA DE FATIMA LAMOEDO' nao encontrado no cadastro
    do PRORADIS

O classificador e first-match-wins e `falha_tecnica` (db.py:1095) vem ANTES de
`paciente_nao_achado` (db.py:1099). O prefixo generico casava primeiro e a causa real,
que estava logo ali no "Detalhe:", nunca era lida.

Consertar a ORDEM da tabela seria remendo: o texto continuaria afirmando "nao e
problema do documento nem da clinica" sobre um paciente que simplesmente nao esta
cadastrado com aquele nome. O conserto e na origem — quando a esteira JA SABE a causa,
ela nao embrulha.

Isto importa pela regra do dono: falha de sistema se resolve por nova tentativa nossa.
Nenhuma re-tentativa faz o paciente aparecer; ele esta cadastrado com outro nome (foi
o caso do VALDEMIR, que o PRORADIS traz como 'VALDEMIR DOS SANTOS PEREIRA' e a guia
chama de 'DOS ANJOS')."""
import db
from esteira import _erro_de_leitura_amigavel


_NAO_CADASTRADO = ("anexos: paciente 'MARIA DE FATIMA LAMOEDO' não encontrado no "
                   "cadastro do PRORADIS — conferir se o nome está escrito igual "
                   "nos dois sistemas")
# Instabilidade DE VERDADE. Antes este exemplo era o "400 INVALID_ARGUMENT" do
# FABRICIO — que desde 23/08 tem classificacao propria (anexo_corrompido,
# Conferencia), porque re-tentar nao conserta arquivo. Ver test_anexo_corrompido.
_GEMINI = "gemini: 503 UNAVAILABLE"


# ── a causa conhecida sai por extenso, sem prefixo generico ───────────────
def test_paciente_nao_cadastrado_nao_vira_falha_tecnica():
    m = _erro_de_leitura_amigavel(_NAO_CADASTRADO)
    assert "falha técnica na leitura" not in m
    assert "não encontrado no cadastro" in m


def test_e_o_classificador_manda_para_o_CADASTRO():
    m = _erro_de_leitura_amigavel(_NAO_CADASTRADO)
    chave, quem, _ = db.classificar_pendencia(m, "erro")
    assert chave == "paciente_nao_achado"
    assert quem == "Cadastro"


def test_sai_do_retry_e_aparece_no_painel():
    m = _erro_de_leitura_amigavel(_NAO_CADASTRADO)
    assert db.eh_nosso(m, "erro") is False
    assert db.eh_pendencia_front(m, "erro") is True
    assert db.deve_entrar_no_retry(m, "erro") is False


# ── falha tecnica de verdade continua sendo nossa ─────────────────────────
def test_erro_do_gemini_continua_generico_e_nosso():
    m = _erro_de_leitura_amigavel(_GEMINI)
    assert "falha técnica na leitura" in m
    assert db.eh_nosso(m, "erro") is True
    assert db.deve_entrar_no_retry(m, "erro") is True


def test_erro_desconhecido_continua_generico():
    m = _erro_de_leitura_amigavel("algo que ninguem previu")
    assert "falha técnica na leitura" in m
    assert "algo que ninguem previu" in m


def test_erro_vazio_nao_quebra():
    assert isinstance(_erro_de_leitura_amigavel(""), str)
    assert isinstance(_erro_de_leitura_amigavel(None), str)


# ── homonimo continua NOSSO: ali o re-run desempata ───────────────────────
def test_homonimo_continua_nosso():
    """Caso ALESSANDRA: com 2+ cards o nascimento desempata num re-run — insistir
    ali FUNCIONA."""
    m = _erro_de_leitura_amigavel("anexos: 2 paciente(s) com o nome 'ALESSANDRA' no "
                                  "PRORADIS — não foi possível identificar")
    assert db.eh_nosso(m, "erro") is True
