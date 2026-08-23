"""Duas telas, dois papeis — e o nome tem que dizer qual e qual.

Pedido do dono (23/08): *"vamos mudar o nome no front end de pendencias para
pendencias abertas. ja la no relatorio mantemos o nome pendencia por datas incluindo
uma descricao de que ali e um historico de pendencias e mesmo que uma pendencia ja
tenha sido resolvida ela ainda vai aparecer ali, e para ver o status atual das
pendencias o usuario deve ir em pendencias abertas. dentro das pendencias abertas
deve ser possivel filtrar por data tambem."*

O problema real: as duas telas se chamavam "Pendencias". A de /relatorios/pendencias
mostra o que foi pendencia NAQUELE dia — inclusive o que ja foi resolvido depois — e
quem olhava ali achava que era o estado de agora. Contar guia resolvida como pendente
faz a operacao caçar trabalho que nao existe.

O filtro de data em /pendencias e o que faltava para a tela ser usavel no dia a dia:
com o prazo de faturamento correndo, a pergunta e "o que vence primeiro", e isso se
responde recortando o periodo."""
import pytest


@pytest.fixture()
def app_cli(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "teste")
    import app as appmod
    appmod.app.config["TESTING"] = True
    return appmod


def _logado(appmod, monkeypatch, role="user", uid=2):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid; s["username"] = "andrea"; s["role"] = role
    monkeypatch.setattr(appmod, "_usuario_valido", lambda u: {"id": u, "role": role})
    return c


# ── o filtro de data e uma funcao pura, testavel sozinha ───────────────────
def test_filtro_recorta_o_periodo():
    from app import _filtra_por_dia
    itens = [{"dia": "17/08/2026"}, {"dia": "18/08/2026"}, {"dia": "19/08/2026"}]
    r = _filtra_por_dia(itens, "2026-08-18", "2026-08-19")
    assert [x["dia"] for x in r] == ["18/08/2026", "19/08/2026"]


def test_filtro_so_com_inicio():
    from app import _filtra_por_dia
    itens = [{"dia": "17/08/2026"}, {"dia": "19/08/2026"}]
    assert len(_filtra_por_dia(itens, "2026-08-18", "")) == 1


def test_filtro_so_com_fim():
    from app import _filtra_por_dia
    itens = [{"dia": "17/08/2026"}, {"dia": "19/08/2026"}]
    assert len(_filtra_por_dia(itens, "", "2026-08-18")) == 1


def test_sem_filtro_devolve_tudo():
    from app import _filtra_por_dia
    itens = [{"dia": "17/08/2026"}, {"dia": "19/08/2026"}]
    assert _filtra_por_dia(itens, "", "") == itens
    assert _filtra_por_dia(itens, None, None) == itens


def test_data_invalida_nao_esconde_nada():
    """Filtro quebrado que devolve lista vazia faz a operacao achar que zerou a
    fila. Na duvida, mostrar tudo."""
    from app import _filtra_por_dia
    itens = [{"dia": "17/08/2026"}]
    assert _filtra_por_dia(itens, "banana", "") == itens


def test_item_sem_data_nao_some_quando_nao_ha_filtro():
    from app import _filtra_por_dia
    itens = [{"dia": ""}, {"dia": "18/08/2026"}]
    assert len(_filtra_por_dia(itens, "", "")) == 2


def test_item_sem_data_sai_quando_ha_filtro():
    """Com periodo pedido, guia sem data nao pode entrar por omissao."""
    from app import _filtra_por_dia
    itens = [{"dia": ""}, {"dia": "18/08/2026"}]
    r = _filtra_por_dia(itens, "2026-08-18", "2026-08-18")
    assert [x["dia"] for x in r] == ["18/08/2026"]


# ── os nomes nas telas ────────────────────────────────────────────────────
def test_tela_se_chama_pendencias_abertas(app_cli, monkeypatch):
    html = _logado(app_cli, monkeypatch).get("/pendencias").get_data(as_text=True)
    assert "Pendências abertas" in html


def test_relatorio_avisa_que_e_historico(app_cli, monkeypatch):
    """Quem abre o relatorio precisa saber que ve o passado, nao o agora."""
    html = _logado(app_cli, monkeypatch).get(
        "/relatorios/pendencias?data=2026-08-18").get_data(as_text=True)
    assert "histórico" in html.lower()
    assert "/pendencias" in html          # manda pro lugar do estado atual


def test_relatorio_diz_que_resolvida_continua_aparecendo(app_cli, monkeypatch):
    html = _logado(app_cli, monkeypatch).get(
        "/relatorios/pendencias?data=2026-08-18").get_data(as_text=True)
    assert "resolvida" in html.lower()


def test_tela_aberta_tem_campo_de_data(app_cli, monkeypatch):
    html = _logado(app_cli, monkeypatch).get("/pendencias").get_data(as_text=True)
    assert 'name="de"' in html and 'name="ate"' in html


def test_filtro_de_data_chega_na_tela(app_cli, monkeypatch):
    r = _logado(app_cli, monkeypatch).get("/pendencias?de=2026-08-18&ate=2026-08-19")
    assert r.status_code == 200
    assert "2026-08-18" in r.get_data(as_text=True)
