"""Duas iniciais abreviadas nao podem virar "outra pessoa".

Caso PRISCILA FARIAS DOS SANTOS DANTAS (GTO 196314423, 397950, 18/08). O prontuario
tem o pedido CERTO — Dra Alana Muller, CRO 29369, data 18/08/2026, exame
"radiografia periapical de boca completa", que e exatamente o que a guia autoriza — e
o laudo do periapical ja esta no plano. A guia nao faturou por causa do nome:

    lido na solicitacao : 'Priscila F. S. Dantas'
    nome da guia        : 'PRISCILA FARIAS DOS SANTOS DANTAS'

A regra da INICIAL ABREVIADA ja existia (caso ISABELA BENINI M. TAVARES, 25/07), mas
so alcanca UM token divergente: o corte `len(se_falta) > 1 -> outra pessoa` roda
ANTES dela. Com duas iniciais ('F.' e 'S.') a guia morre no corte.

O erro conceitual e tratar inicial como DIVERGENCIA. 'F.' nao contradiz 'FARIAS' —
nao carrega informacao que possa contradizer nada. Divergencia e 'PEDRO' contra
'JOAO'. A inicial tem de ser resolvida no pareamento, junto com o erro de grafia, e
nao depois da contagem.

A trava anti-irmao continua de pe: nada disso e alcancado sem 2+ tokens IDENTICOS, e
cada inicial consome UM token livre distinto."""
from esteira import _nomes_compat


# ── o caso ────────────────────────────────────────────────────────────────
def test_priscila_duas_iniciais():
    assert _nomes_compat("Priscila F. S. Dantas",
                         "PRISCILA FARIAS DOS SANTOS DANTAS") is True


def test_uma_inicial_continua_valendo():
    """Caso ISABELA, 25/07 — nao pode regredir."""
    assert _nomes_compat("Isabela Benini M. Tavares",
                         "ISABELA BENINI MEDINA TAVARES") is True


def test_inicial_sem_ponto():
    assert _nomes_compat("Priscila F S Dantas",
                         "PRISCILA FARIAS DOS SANTOS DANTAS") is True


# ── a trava anti-irmao NAO pode afrouxar ──────────────────────────────────
def test_irmao_com_inicial_que_nao_bate():
    """'P.' nao casa com JOAO. Continua sendo outra pessoa."""
    assert _nomes_compat("P. Silva Santos", "JOAO SILVA SANTOS") is False


def test_irmao_com_nome_inteiro_diferente():
    """A guarda original (PEDRO x JOAO) segue intacta."""
    assert _nomes_compat("Pedro Silva Santos", "JOAO SILVA SANTOS") is False


def test_inicial_nao_substitui_a_exigencia_de_dois_tokens_iguais():
    """So sobrenome + inicial nao identifica ninguem: 'J. SILVA' casaria com meio
    Brasil. Exige 2+ tokens identicos antes de qualquer relaxamento."""
    assert _nomes_compat("J. Silva", "JOAO SILVA") is False


def test_cada_inicial_precisa_do_seu_proprio_token_livre():
    """Duas iniciais DIFERENTES precisam de dois tokens livres distintos. 'F.' come
    FARIAS; sobra 'Z.' sem nada que comece por Z -> divergente, recusa."""
    assert _nomes_compat("Ana F. Z. Costa", "ANA FARIAS COSTA") is False


def test_inicial_que_nao_corresponde_a_nenhum_token_livre():
    assert _nomes_compat("Priscila X. Z. Dantas",
                         "PRISCILA FARIAS DOS SANTOS DANTAS") is False


# ── nao mexe no que ja funcionava ─────────────────────────────────────────
def test_nome_exato():
    assert _nomes_compat("PRISCILA FARIAS DOS SANTOS DANTAS",
                         "PRISCILA FARIAS DOS SANTOS DANTAS") is True


def test_erro_de_grafia_continua():
    assert _nomes_compat("Sophia Carvallo do Rosamo",
                         "SOPHIA CARVALHO DO ROSARIO") is True


def test_documento_de_outra_pessoa_continua_recusado():
    """Caso HOSANA/GLADYS — o gate de identidade nao pode ceder."""
    assert _nomes_compat("GLADYS FREITAS DOS SANTOS",
                         "HOSANA BARRETO DOS SANTOS") is False
