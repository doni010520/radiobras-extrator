"""Toda pendencia cujo bloqueio e a IDENTIDADE precisa do botao de confirmar.

Regressao de 23/08, introduzida por mim. ANETE ANDRADE DE MATTOS (196254671) e
CASSIANA DOS SANTOS NASCIMENTO (196408957) sairam de `nome_nao_bate` para o grupo
novo `prenome_mal_lido` — roteamento certo, porque nelas TODOS os sobrenomes batem e
so o primeiro nome difere ('Plunet Andrade de Mattos'), o que e erro de leitura e nao
documento de terceiro.

So que o botao "✔ Confirmei que e o paciente" e renderizado por uma whitelist de
chaves em templates/pendencias.html, e a chave nova nao entrou nela. Resultado: as
duas guias trocaram um rotulo errado por uma tela SEM SAIDA — a operadora ve a
pendencia, ve o pedido, concorda que e a paciente e nao tem onde clicar. Ficaram
menos acionaveis do que antes da "correcao".

O invariante: se o unico bloqueio e "de quem e este papel?", tem de existir o clique
que responde isso. Se o bloqueio e outra coisa (data vencida, pedido que nao cobre,
laudo faltando), o botao NAO deve aparecer — ele libera anexacao irreversivel."""
import io
import re

import db

_TPL = io.open("templates/pendencias.html", encoding="utf-8").read()


def _whitelist():
    """As chaves que renderizam o botao de confirmar, lidas do template."""
    m = re.search(r"\{%\s*if p\.chave in \(([^)]*)\)[^%]*conf|"
                  r"\{%\s*if p\.chave in \(([^)]*)\)", _TPL)
    assert m, "nao achei a whitelist do botao no template"
    return set(re.findall(r"'([a-z_]+)'", m.group(0)))


# ── as chaves de IDENTIDADE tem botao ──────────────────────────────────────
def test_prenome_mal_lido_tem_botao():
    """A regressao. O bloqueio e 'so o prenome nao bate' — puro olho humano."""
    assert "prenome_mal_lido" in _whitelist()


def test_as_outras_chaves_de_identidade_continuam_com_botao():
    w = _whitelist()
    for chave in ("pedido_ilegivel", "solic_nao_confirmada", "nome_nao_bate"):
        assert chave in w, chave


# ── chaves que NAO sao de identidade nao podem ter o botao ─────────────────
def test_bloqueio_que_nao_e_identidade_nao_ganha_botao():
    """O botao libera anexacao real e irreversivel. Numa guia parada por falta de
    laudo ou por pedido que nao cobre, confirmar a pessoa nao resolve nada e so
    cria caminho para faturar errado."""
    w = _whitelist()
    for chave in ("falta_laudo", "esperando_tele", "pedido_nao_cobre",
                  "sem_pedido", "sem_entregavel", "modelo_sem_render"):
        assert chave not in w, chave


# ── o grupo existe mesmo (liga o template a tabela de classificacao) ───────
def test_a_chave_prenome_mal_lido_e_produzida_pelo_classificador():
    """Se o classificador parar de emitir a chave, o teste acima viraria um teste
    sobre uma string morta."""
    msg = ("NÃO FATUROU porque o nome lido no pedido não bate com o da guia — mas "
           "TODOS OS SOBRENOMES BATEM e só o primeiro nome difere, o que é a cara "
           "de erro de leitura do prenome.")
    chave, quem, _ = db.classificar_pendencia(msg, "sem_solicitacao")
    assert chave == "prenome_mal_lido"
    assert quem == "Conferência"
