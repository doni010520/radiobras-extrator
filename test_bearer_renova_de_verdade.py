"""Renovar o Bearer tem que devolver um token NOVO — nunca o mesmo que morreu.

`test_jwt_renova.py` ja garante que um 401 dispara a renovacao. Mas na producao a
renovacao nao renovava nada: `_renovar_bearer` (dentro do worker de anexacao) faz

    def _renovar_bearer():
        return _bearer["v"]

e `_bearer["v"]` e o token capturado PASSIVAMENTE do ultimo request do navegador
daquele worker. Se o navegador nao fez request novo desde o login — exatamente o
caso quando a anexacao roda 20+ min depois, sob throttle — `_bearer["v"]` E O MESMO
TOKEN VENCIDO. A renovacao devolve o token morto, o retry leva outro 401, e a guia
cai em "nao consegui ler quantos anexos a guia ja tem (DOM e API falharam: HTTP 401
'Jwt is expired')".

Medido em 12/09 sobre 30 dias de execucoes: 31 guias barradas exatamente assim — a
maior causa isolada de falha NOSSA no anexador.

Devolver o token que acabou de falhar nao e renovar. Se a captura esta velha, tem
que FORCAR trafego novo no navegador (que reemite o Authorization) e so entao
devolver. Sem token novo, devolve None — e ai a trava bloqueia, como ja bloqueava.
"""
import esteira


def test_captura_diferente_do_que_falhou_serve():
    """Caso feliz: o navegador ja emitiu request novo, a captura vale."""
    assert esteira._bearer_renovado("Bearer NOVO", "Bearer VELHO") == "Bearer NOVO"


def test_captura_igual_ao_token_morto_nao_e_renovacao():
    """O coracao do bug: devolver o mesmo token vencido nao renova nada."""
    assert esteira._bearer_renovado("Bearer VELHO", "Bearer VELHO") is None


def test_captura_velha_forca_trafego_novo_e_devolve_o_token_fresco():
    """Quando a captura esta velha, forcar() faz o navegador reemitir o header."""
    estado = {"v": "Bearer VELHO"}

    def _forcar():
        estado["v"] = "Bearer FRESCO"

    assert esteira._bearer_renovado(
        "Bearer VELHO", "Bearer VELHO",
        forcar=_forcar, recapturar=lambda: estado["v"]) == "Bearer FRESCO"


def test_forcar_que_nao_traz_token_novo_devolve_none():
    """Navegador morto/sessao caida: sem token novo, nao inventa — devolve None e a
    trava de duplicidade bloqueia (nada enviado), que e o comportamento seguro."""
    assert esteira._bearer_renovado(
        "Bearer VELHO", "Bearer VELHO",
        forcar=lambda: None, recapturar=lambda: "Bearer VELHO") is None


def test_forcar_que_levanta_nao_derruba_o_worker():
    """Navegar pode falhar (pagina fechada, proxy). Degrada para None, nunca sobe
    excecao dentro do worker de anexacao."""
    def _boom():
        raise RuntimeError("page closed")

    assert esteira._bearer_renovado(
        "Bearer VELHO", "Bearer VELHO",
        forcar=_boom, recapturar=lambda: "Bearer VELHO") is None


def test_sem_captura_nenhuma_devolve_none():
    assert esteira._bearer_renovado(None, "Bearer VELHO") is None
    assert esteira._bearer_renovado("", "Bearer VELHO") is None
