"""'Nenhum documento no nome do paciente': de QUEM e essa pendencia?

Hoje as duas situacoes abaixo caem no mesmo grupo `nome_nao_bate`, marcado como
"Nos" — some do painel e fica presa no retry para sempre. Nenhuma das duas e nossa:

  A) SO O PRENOME difere; todos os sobrenomes batem exatamente.
     ANETE ANDRADE DE MATTOS      <- 'Plunet Andrade de Mattos'
     CASSIANA DOS SANTOS NASCIMENTO <- 'Camara dos Santos Nascimento'
     E leitura do prenome que falhou. A IA LEU e a leitura nao casou -> pendencia
     da CLINICA (regra do dono, 23/08): re-tentar nao faz nome divergente casar, e
     quem pode anexar um pedido com o nome legivel e ela. O botao "Confirmei que e o
     paciente" segue na tela como atalho para quem conferir o papel.

  B) PRENOME E SOBRENOME diferem — e documento de OUTRA PESSOA.
     HOSANA BARRETO DOS SANTOS    <- 'GLADYS FREITAS DOS SANTOS'
     LUCIANA SOUZA SANTOS         <- 'S. Santos'
     GEORGE CARVALHO SILVA DOS REIS <- 'BRONGE CANDIDA SILVA DOS REIS'
     Verificado na HOSANA: o texto do pedido diz "Para Sr(a): GLADYS FREITAS DOS
     SANTOS", com nascimento 12/11/1972 — outra pessoa mesmo. O robo recusou CERTO
     (caso JOCASTA). O que falta e a clinica anexar o pedido DESTA paciente.

NADA AQUI AFROUXA O GATE DE IDENTIDADE. A regra nao faz nenhum documento passar a
ser aceito — ela so decide o TEXTO e o DONO da pendencia, para a guia sair do limbo
e virar acao de alguem."""
from db import so_o_prenome_difere


def test_anete_e_erro_de_leitura_do_prenome():
    assert so_o_prenome_difere("ANETE ANDRADE DE MATTOS",
                               "Plunet Andrade de Mattos") is True


def test_cassiana_tambem():
    assert so_o_prenome_difere("CASSIANA DOS SANTOS NASCIMENTO",
                               "Camara dos Santos Nascimento") is True


def test_hosana_e_outra_pessoa():
    """Sobrenome do meio diferente (BARRETO x FREITAS). 'DOS SANTOS' em comum nao
    vale nada — e o sobrenome mais comum do Brasil."""
    assert so_o_prenome_difere("HOSANA BARRETO DOS SANTOS",
                               "GLADYS FREITAS DOS SANTOS") is False


def test_george_e_outra_pessoa():
    """CARVALHO x CANDIDA: dois tokens diferentes, nao so o prenome."""
    assert so_o_prenome_difere("GEORGE CARVALHO SILVA DOS REIS",
                               "BRONGE CANDIDA SILVA DOS REIS") is False


def test_nome_truncado_nao_serve():
    """'S. Santos' nao da para afirmar nada — e um sobrenome so."""
    assert so_o_prenome_difere("LUCIANA SOUZA SANTOS", "S. Santos") is False


def test_conectivos_nao_contam():
    """DE / DOS / DA / E sao ruido; 'ANDRADE DE MATTOS' e 'Andrade Mattos' sao o
    mesmo sobrenome."""
    assert so_o_prenome_difere("ANETE ANDRADE DE MATTOS",
                               "Plunet Andrade Mattos") is True


def test_precisa_de_pelo_menos_dois_sobrenomes():
    """Com um sobrenome so, 'JOAO SILVA' x 'PEDRO SILVA' seriam 'a mesma pessoa'.
    Metade do Brasil casaria. Exige 2+."""
    assert so_o_prenome_difere("JOAO SILVA", "PEDRO SILVA") is False


def test_nome_igual_nao_e_caso_desta_regra():
    # se batem, nem chega aqui — mas a funcao nao pode dizer 'so o prenome difere'
    assert so_o_prenome_difere("ANA MARIA SOUZA", "ANA MARIA SOUZA") is False


def test_entrada_vazia():
    assert so_o_prenome_difere("", "ALGUEM") is False
    assert so_o_prenome_difere("ALGUEM", "") is False
    assert so_o_prenome_difere(None, None) is False


def test_acento_e_caixa_nao_atrapalham():
    assert so_o_prenome_difere("JOSÉ GONÇALVES PEREIRA",
                               "Joso Gonçalves Pereira") is True
    assert so_o_prenome_difere("JOSÉ ANTÔNIO GONÇALVES PEREIRA",
                               "Joso Antonio Goncalves Pereira") is True


def test_prenome_composto_com_erro_no_SEGUNDO_nome_nao_passa():
    """Prenome brasileiro e frequentemente composto. A regra olha o PRIMEIRO token
    so; se o erro estiver no segundo ('ANA MAIRA'), ele conta como sobrenome
    diferente e a guia cai no balde de 'outra pessoa'. E o lado seguro: no maximo
    mandamos conferir uma que era erro de leitura — nunca aceitamos documento de
    terceiro."""
    assert so_o_prenome_difere("ANA MARIA SOUZA LIMA",
                               "ANA MAIRA SOUZA LIMA") is False


# ── a mensagem cai no grupo certo ───────────────────────────────────────────
_MSG_PRENOME = ("NÃO FATUROU porque o nome lido no pedido não bate com o da guia — "
                "mas TODOS OS SOBRENOMES BATEM e só o primeiro nome difere, o que é "
                "a cara de erro de leitura do prenome (letra de médico, carimbo "
                "borrado). O documento provavelmente É deste paciente.")
_MSG_OUTRO = ("NÃO FATUROU porque nenhum documento do prontuário está no nome deste "
              "paciente. O prontuário tem anexos, mas o nome lido em cada um é de "
              "OUTRA pessoa")


def test_prenome_mal_lido_vai_para_a_CLINICA():
    """Regra do dono, 23/08: *"se existe uma divergencia de leitura entre o nome do
    paciente e o nome lido na solicitacao, a IA na verdade fez a leitura mas a
    leitura nao casa. entao e uma falha tecnica... isso deve virar pendencia para a
    clinica resolver."*

    O ponto: a IA LEU. Nao e 'documento ilegivel' nem 'nao encontrei nada' — a
    leitura saiu e simplesmente nao bate. Re-tentar nunca faz um nome divergente
    passar a casar, entao segurar na fila tecnica prende a guia para sempre. Quem
    resolve e a clinica, anexando um pedido com o nome legivel.

    Passou por 'Nos' (sumia do painel, preso no retry) e por 'Conferencia' antes de
    chegar aqui. O botao de confirmar continua na tela como atalho para quem
    conferir o papel — o DONO da pendencia e que mudou."""
    from db import classificar_pendencia, eh_nosso, eh_pendencia_front, classe_retry
    chave, quem, acao = classificar_pendencia(_MSG_PRENOME, "sem_solicitacao")
    assert chave == "prenome_mal_lido"
    assert quem == "Clínica"
    assert eh_nosso(_MSG_PRENOME, "sem_solicitacao") is False
    assert eh_pendencia_front(_MSG_PRENOME, "sem_solicitacao") is True
    assert classe_retry(_MSG_PRENOME, "sem_solicitacao") == "externo"
    assert "clínica" in acao.lower()


def test_documento_de_outro_paciente_continua_como_estava():
    """O caso HOSANA/JOCASTA nao pode mudar de grupo por causa desta regra."""
    from db import classificar_pendencia
    assert classificar_pendencia(_MSG_OUTRO, "sem_solicitacao")[0] == "nome_nao_bate"


def test_documento_de_outro_e_da_CLINICA_e_aparece_no_painel():
    """Verificado na HOSANA (23/08): o prontuario tinha pedido de GLADYS FREITAS
    DOS SANTOS, nascimento 12/11/1972 — outra pessoa. A recusa esta certa; o que
    falta e a CLINICA anexar o pedido desta paciente.

    Marcada como 'Nos', a guia sumia do painel e ficava presa no retry re-tentando
    o que nunca muda sozinho — 6 tentativas e uma escalacao as 07:09 por nada."""
    from db import classificar_pendencia, eh_nosso, eh_pendencia_front, classe_retry
    chave, quem, acao = classificar_pendencia(_MSG_OUTRO, "sem_solicitacao")
    assert chave == "nome_nao_bate"
    assert quem == "Clínica"
    assert eh_nosso(_MSG_OUTRO, "sem_solicitacao") is False
    assert eh_pendencia_front(_MSG_OUTRO, "sem_solicitacao") is True
    assert classe_retry(_MSG_OUTRO, "sem_solicitacao") == "externo"
    assert "glosa" in acao.lower()


def test_nao_entra_mais_no_retry():
    """Externo nunca entra no loop — re-tentar nao faz a clinica anexar."""
    from db import deve_entrar_no_retry
    assert deve_entrar_no_retry(_MSG_OUTRO, "sem_solicitacao") is False
