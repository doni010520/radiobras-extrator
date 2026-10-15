"""E-mails no formato aprovado pelo dono (15/10)."""
import emails_rb as E

CLS = lambda m, c: (("sem_pedido", "Clínica", "Pedir à clínica o pedido.") if "pedido" in (m or "")
                    else ("falta_laudo", "Radiologista", "Cobrar o laudo."))
NOSSO = lambda m, c: "créditos" in (m or "")
SLA = {"14/10/2026": 6, "09/10/2026": 1, "08/10/2026": 0, "01/10/2026": -2}.get


def _p(gto, dia, motivo, pac="FULANO"):
    return {"gto": gto, "dia": dia, "motivo": motivo, "categoria": "x", "conta": "388336", "paciente": pac}


def test_prazo_agrupa_por_quem_e_tira_falha_nossa_e_vencidas():
    ps = [_p("1", "09/10/2026", "falta pedido"), _p("2", "08/10/2026", "falta laudo"),
          _p("3", "09/10/2026", "créditos acabaram"), _p("4", "01/10/2026", "falta laudo"),
          _p("5", "14/10/2026", "falta laudo")]
    a, t, h = E.montar_email_prazo(ps, SLA, lambda c: "Centro", CLS, NOSSO)
    assert "2 guia(s)" in a and "Clínica 1" in a and "Radiologia 1" in a
    assert "guia 3" not in t and "guia 5" not in t       # nossa e fora da janela
    assert "1 guia(s) já passaram do prazo" in t         # vencida vira 1 linha


def test_prazo_sem_ninguem_para_agir_nao_envia():
    assert E.montar_email_prazo([_p("3", "09/10/2026", "créditos")], SLA, str, CLS, NOSSO)[0] is None


def test_resumo_conta_por_guia_e_nao_por_execucao():
    linhas = [{"gto": "1", "conta": "a", "categoria": "auto", "faturado": True},
              {"gto": "1", "conta": "a", "categoria": "ja_anexada", "faturado": True},
              {"gto": "1", "conta": "a", "categoria": "ja_anexada", "faturado": True},
              {"gto": "2", "conta": "a", "categoria": "ja_anexada", "faturado": True},
              {"gto": "3", "conta": "a", "categoria": "sem_solicitacao", "faturado": False}]
    d = E.classificar_guias(linhas)
    assert d == {("a", "1"): "robo_pedido", ("a", "2"): "outra_pessoa", ("a", "3"): "nao_faturada"}
    a, t, h = E.montar_resumo_semana(d, [], lambda c: "Centro", SLA, CLS, NOSSO, "08/10", "15/10")
    assert "1 guia(s) faturada(s) pelo robô" in a
