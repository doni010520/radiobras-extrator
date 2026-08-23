"""Nome ABREVIADO nao e nome de OUTRA pessoa — e a diferenca custa faturamento.

Reauditoria de 23/08, depois de 12 deploys. Tres guias estavam a UM CLIQUE de faturar
e o painel mandava esperar a clinica:

  ANETE ANDRADE DE MATTOS (196254671)      lido 'Plunet Andrade de Mattos'
  CASSIANA DOS SANTOS NASCIMENTO (196408957) lido 'Camara dos Santos Nascimento'
  LUCIANA SOUZA SANTOS (196397719)         lido 'S. Santos'

Nas tres o pedido esta no prontuario e COBRE a guia: `_escolher_solicitacao` com
`nome_confirmado=True` devolve idx=0, motivo=None. O botao "Confirmei que e o
paciente" ja aparece na tela. So o DONO estava errado.

DOIS AJUSTES.

1. `prenome_mal_lido` volta para Conferencia. A regra do dono (23/08) era que
   divergencia de leitura NAO pode ficar presa como falha nossa no retry, invisivel —
   e nao fica: Conferencia e visivel, acionavel e `eh_nosso=False`. Mandar para a
   clinica um pedido que ja esta anexado custa dias de espera por papel que existe.
   O texto passa a levar ao clique, com a clinica como saida SECUNDARIA.

2. Grupo novo `nome_abreviado`, ANTES de nome_nao_bate. O texto de nome_nao_bate
   AFIRMA "o prontuario so tem documento de OUTRO paciente" e "nunca anexar documento
   de terceiro: gera glosa". Para a HOSANA isso e verdade e esta provado — o papel diz
   por extenso "Para Sr(a): GLADYS FREITAS DOS SANTOS", com nascimento 12/11/1972.
   Para a LUCIANA e afirmacao sem prova nenhuma: 'S. Santos' e inicial + sobrenome,
   compativel com L. S. Santos, dentro do prontuario DELA.

   Acusar a clinica de anexar documento de terceiro sem prova e pior do que nao
   classificar: manda cobrar o que talvez nao seja erro dela, e esconde um
   faturamento que estava a um clique."""
import db


_ABREV = ("NÃO FATUROU porque o nome lido no pedido está ABREVIADO ('S. Santos') e "
          "não dá para afirmar que é o paciente — nem que não é.")
_OUTRO = ("NÃO FATUROU porque nenhum documento do prontuário está no nome deste "
          "paciente. O prontuário tem anexos, mas o nome lido em cada um é de "
          "OUTRA pessoa")
_PRENOME = ("NÃO FATUROU porque o nome lido no pedido não bate com o da guia — mas "
            "TODOS OS SOBRENOMES BATEM e só o primeiro nome difere, o que é a cara "
            "de erro de leitura do prenome.")


# ── nome abreviado: Conferencia, nao acusacao ─────────────────────────────
def test_nome_abreviado_vai_para_conferencia():
    chave, quem, acao = db.classificar_pendencia(_ABREV, "sem_solicitacao")
    assert chave == "nome_abreviado"
    assert quem == "Conferência"


def test_nome_abreviado_nao_acusa_terceiro():
    _, _, acao = db.classificar_pendencia(_ABREV, "sem_solicitacao")
    assert "OUTRA pessoa" not in acao
    assert "outro paciente" not in acao.lower()


def test_nome_abreviado_manda_conferir_antes_de_cobrar():
    _, _, acao = db.classificar_pendencia(_ABREV, "sem_solicitacao")
    assert "confer" in acao.lower() or "abrir" in acao.lower()


def test_nome_abreviado_aparece_e_sai_do_retry():
    assert db.eh_pendencia_front(_ABREV, "sem_solicitacao") is True
    assert db.eh_nosso(_ABREV, "sem_solicitacao") is False
    assert db.deve_entrar_no_retry(_ABREV, "sem_solicitacao") is False


# ── documento PROVADAMENTE de terceiro nao muda ───────────────────────────
def test_hosana_continua_como_documento_de_outro():
    """Ali ha prova: o papel diz o nome completo e o nascimento."""
    chave, quem, acao = db.classificar_pendencia(_OUTRO, "sem_solicitacao")
    assert chave == "nome_nao_bate"
    assert quem == "Clínica"
    assert "glosa" in acao.lower()


# ── prenome mal lido volta para Conferencia ───────────────────────────────
def test_prenome_mal_lido_e_conferencia():
    chave, quem, acao = db.classificar_pendencia(_PRENOME, "sem_solicitacao")
    assert chave == "prenome_mal_lido"
    assert quem == "Conferência"


def test_prenome_mal_lido_leva_ao_clique_e_cita_a_clinica_depois():
    _, _, acao = db.classificar_pendencia(_PRENOME, "sem_solicitacao")
    a = acao.lower()
    assert "confirm" in a, "tem de levar ao botao"
    assert "cl\u00ednica" in a or "clinica" in a, "a clinica fica como saida secundaria"


def test_prenome_mal_lido_continua_visivel_e_fora_do_retry():
    """A regra do dono: divergencia de leitura nao pode ficar presa como falha nossa
    invisivel. Conferencia satisfaz — nao e nossa, aparece, e tem acao."""
    assert db.eh_nosso(_PRENOME, "sem_solicitacao") is False
    assert db.eh_pendencia_front(_PRENOME, "sem_solicitacao") is True
    assert db.deve_entrar_no_retry(_PRENOME, "sem_solicitacao") is False


# ── o botao tem de existir para as tres chaves ────────────────────────────
def test_as_tres_chaves_tem_botao_de_confirmar():
    import io
    tpl = io.open("templates/pendencias.html", encoding="utf-8").read()
    i = tpl.index("confirmar({{ p.id }}")
    bloco = tpl[max(0, i - 1200):i]
    for chave in ("prenome_mal_lido", "nome_nao_bate", "nome_abreviado"):
        assert chave in bloco, chave


# ══════════════════════════════════════════════════════════════════════════
# A ESTEIRA precisa EMITIR o texto — senao o grupo novo nunca casa.
#
# Depois de criar `nome_abreviado`, ANETE e CASSIANA migraram para Conferencia mas a
# LUCIANA (196397719) NAO: o grupo casa num texto que a esteira ainda nao produzia.
# Classificar e so metade; quem escreve o motivo e a esteira.
#
# O sinal: o nome lido tem MENOS tokens significativos que o da guia e os que tem sao
# compativeis (inicial ou sobrenome que aparece na guia). 'S. Santos' contra 'LUCIANA
# SOUZA SANTOS'. Isso nao prova identidade — nem prova o contrario, e e essa a
# diferenca que separa "conferir" de "acusar a clinica".
# ══════════════════════════════════════════════════════════════════════════

from esteira import _nome_apenas_abreviado


def test_luciana_e_nome_abreviado():
    assert _nome_apenas_abreviado([{"paciente_lido": "S. Santos"}],
                                  "LUCIANA SOUZA SANTOS") is True


def test_inicial_sem_ponto_tambem():
    assert _nome_apenas_abreviado([{"paciente_lido": "L S Santos"}],
                                  "LUCIANA SOUZA SANTOS") is True


def test_nome_completo_de_outra_pessoa_NAO_e_abreviado():
    """Caso HOSANA/GLADYS: nome inteiro, de terceiro. Tem de continuar acusando."""
    assert _nome_apenas_abreviado([{"paciente_lido": "GLADYS FREITAS DOS SANTOS"}],
                                  "HOSANA BARRETO DOS SANTOS") is False


def test_sobrenome_que_nao_existe_na_guia_NAO_e_abreviado():
    assert _nome_apenas_abreviado([{"paciente_lido": "M. Pereira"}],
                                  "LUCIANA SOUZA SANTOS") is False


def test_nome_completo_compativel_NAO_e_abreviado():
    """Se bate inteiro, nem chega aqui — e nao pode ser rotulado de abreviado."""
    assert _nome_apenas_abreviado([{"paciente_lido": "LUCIANA SOUZA SANTOS"}],
                                  "LUCIANA SOUZA SANTOS") is False


def test_prenome_mal_lido_NAO_e_abreviado():
    """'Plunet Andrade de Mattos' tem o mesmo numero de tokens — e outra categoria."""
    assert _nome_apenas_abreviado([{"paciente_lido": "Plunet Andrade de Mattos"}],
                                  "ANETE ANDRADE DE MATTOS") is False


def test_leitura_vazia_nao_quebra():
    assert _nome_apenas_abreviado([], "ALGUEM") is False
    assert _nome_apenas_abreviado(None, None) is False
    assert _nome_apenas_abreviado([{"paciente_lido": ""}], "ALGUEM") is False


def test_basta_um_anexo_abreviado():
    leituras = [{"paciente_lido": "OUTRO NOME QUALQUER AQUI"},
                {"paciente_lido": "S. Santos"}]
    assert _nome_apenas_abreviado(leituras, "LUCIANA SOUZA SANTOS") is True
