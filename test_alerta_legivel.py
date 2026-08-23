"""As mensagens sao para o DONO ler no celular, nao para mim depurar.

Feedback dele em 23/08, depois de receber 18 alertas: *"esses alertas estao
confusos, eu nao estou entendendo muita coisa a partir deles"*.

Os defeitos, olhando uma mensagem real:

    ⚠️ RadioBras — 8 guia(s) com falha nossa
    *Dia:* 18/08/2026   *Unidade:* 397950            <- codigo, nao nome
    • *196329949* — MATHEUS BRAGA DOS SANTOS
    _Documentacao OK, mas a anexacao falhou: Linha da GTO 196329949 nao encontrada._
    ... mais 7 iguais                                <- 8x o mesmo texto interno
    Fila tecnica: https://.../relatorios/pendencias   <- a tela DA OPERACAO

  1. `397950` nao diz nada. O nome e "RedeUna — Tancredo".
  2. Oito guias com a MESMA causa viram oito paragrafos do texto cru do robo.
  3. Nao diz se ele precisa fazer algo. Ele acorda, le e nao sabe se levanta.
  4. O link levava para a tela da Andrea; a secao tecnica so aparece la para admin,
     entao ele caia no meio das pendencias DELA e tinha que cacar a dele."""
from notificador import _nome_unidade, _resumir_causa, _agrupar_por_causa


# ── unidade por NOME ────────────────────────────────────────────────────────
def test_conta_vira_nome_da_unidade():
    assert "Tancredo" in _nome_unidade("397950")
    assert "Camaçari" in _nome_unidade("410923")
    assert "Centro" in _nome_unidade("388336")


def test_conta_desconhecida_nao_quebra():
    assert _nome_unidade("999999") == "999999"
    assert _nome_unidade("") == "?"
    assert _nome_unidade(None) == "?"


# ── causa em uma linha, em portugues ───────────────────────────────────────
def test_causa_da_lista_perdida():
    m = "Documentação OK, mas a anexação falhou: Linha da GTO 196329949 não encontrada."
    assert _resumir_causa(m) == "o portal não abriu a guia"


def test_causa_do_token_vencido():
    m = ("Documentação OK, mas a anexação falhou: nao consegui ler quantos anexos a "
         "guia ja tem (DOM e API falharam: HTTP 401 'Jwt is expired')")
    assert _resumir_causa(m) == "o acesso ao portal venceu no meio da rodada"


def test_causa_do_proxy():
    m = "anexação falhou (DOM e API falharam: ProxyError: Max retries exceeded)"
    assert _resumir_causa(m) == "o proxy do OdontoPrev caiu"


def test_causa_da_leitura():
    m = "NÃO FATUROU por falha técnica na leitura dos documentos. Detalhe: gemini: 503"
    assert _resumir_causa(m) == "a leitura dos documentos falhou"


def test_causa_desconhecida_devolve_o_comeco_do_texto():
    """Nunca inventar: causa que eu nao mapeei aparece como veio, cortada."""
    m = "NÃO FATUROU por um motivo que ninguém previu ainda, com detalhes longos"
    r = _resumir_causa(m)
    assert r.startswith("NÃO FATUROU por um motivo")
    assert len(r) <= 90


# ── agrupar: 8 guias com a mesma causa viram UMA linha ─────────────────────
def test_agrupa_oito_guias_iguais():
    """O caso real do #613: 8 guias, uma causa so."""
    itens = [{"gto": str(i), "paciente": "P%d" % i,
              "motivo": "anexação falhou: Linha da GTO %d não encontrada." % i}
             for i in range(8)]
    g = _agrupar_por_causa(itens)
    assert len(g) == 1
    causa, guias = g[0]
    assert causa == "o portal não abriu a guia"
    assert len(guias) == 8


def test_causas_diferentes_ficam_separadas():
    itens = [
        {"gto": "1", "paciente": "A", "motivo": "anexação falhou: Linha da GTO 1 não encontrada."},
        {"gto": "2", "paciente": "B", "motivo": "gemini: 503 na leitura"},
        {"gto": "3", "paciente": "C", "motivo": "anexação falhou: Linha da GTO 3 não encontrada."},
    ]
    g = _agrupar_por_causa(itens)
    assert len(g) == 2
    assert dict((c, len(v)) for c, v in g)["o portal não abriu a guia"] == 2


def test_maior_grupo_vem_primeiro():
    itens = ([{"gto": "x", "paciente": "X", "motivo": "gemini: 503"}] +
             [{"gto": str(i), "paciente": "P", "motivo": "Linha da GTO %d não encontrada." % i}
              for i in range(5)])
    g = _agrupar_por_causa(itens)
    assert g[0][0] == "o portal não abriu a guia"
    assert len(g[0][1]) == 5


def test_lista_vazia():
    assert _agrupar_por_causa([]) == []
    assert _agrupar_por_causa(None) == []
