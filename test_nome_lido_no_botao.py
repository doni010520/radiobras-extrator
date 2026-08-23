"""Antes de confirmar identidade, a operadora tem de VER o nome que a IA leu.

O botao "✔ Confirmei que e o paciente" dispara anexacao real e irreversivel. Hoje ele
aparece com o aviso generico "confira que o nome e do paciente", enquanto o nome
efetivamente lido no documento fica dentro de um <details> COLAPSADO mais acima.

Os dois casos abertos em 23/08 mostram o custo disso:

  HOSANA BARRETO DOS SANTOS (196346585) — o unico pedido do prontuario diz, com
  todas as letras, "Para Sr(a): GLADYS FREITAS DOS SANTOS", nascimento 12/11/1972.
  Um clique ali anexa documento de terceiro (caso JOCASTA) e gera glosa.

  LUCIANA SOUZA SANTOS (196397719) — pedido unico, lido como 'S. Santos', datado de
  2023 para um exame de 2026.

Nenhum dos dois deve ser confirmado. Com o nome lido ao lado do botao a recusa fica
obvia; escondido, o clique e as cegas.

Nao remove o botao: existe caso legitimo (nome social, nome de casada) em que a
pessoa olha o papel e confirma com razao. O que muda e a operadora chegar nele
sabendo o que esta escrito no documento."""
import db


_HOSANA = ("[0/solicitacao] paciente='GLADYS FREITAS DOS SANTOS' dentista='Dra X' "
           "CRO=123 data=18/08/2026 texto='SOLICITACAO Para Sr(a): GLADYS FREITAS "
           "DOS SANTOS nascimento 12/11/1972'")
_PRISCILA = ("[0/solicitacao] paciente='Priscila F. S. Dantas' dentista='Dra Alana' "
             "| [1/documento] paciente='PRISCILA FARIAS DOS SANTOS DANTAS' texto='RG'")


def test_extrai_o_nome_de_outra_pessoa():
    assert db.nomes_lidos_resumo(_HOSANA) == ["GLADYS FREITAS DOS SANTOS"]


def test_extrai_varios_sem_repetir():
    r = db.nomes_lidos_resumo(_PRISCILA)
    assert "Priscila F. S. Dantas" in r
    assert "PRISCILA FARIAS DOS SANTOS DANTAS" in r
    assert len(r) == len(set(r))


def test_nao_repete_o_mesmo_nome_em_anexos_diferentes():
    dois = ("[0/solicitacao] paciente='S. Santos' texto='a' | "
            "[1/solicitacao] paciente='S. Santos' texto='b'")
    assert db.nomes_lidos_resumo(dois) == ["S. Santos"]


def test_leitura_vazia_nao_inventa():
    assert db.nomes_lidos_resumo("") == []
    assert db.nomes_lidos_resumo(None) == []
    assert db.nomes_lidos_resumo("[0/documento] texto='sem campo paciente'") == []


def test_ignora_nome_vazio():
    assert db.nomes_lidos_resumo("[0/solicitacao] paciente='' texto='x'") == []


def test_o_botao_mostra_o_nome_lido():
    """Liga o helper a tela: de nada adianta extrair e nao renderizar."""
    import io
    tpl = io.open("templates/pendencias.html", encoding="utf-8").read()
    i = tpl.index("confirmar({{ p.id }}")     # o botao, nao o bloco de CSS
    bloco = tpl[max(0, i - 900):i]              # o que a operadora le ANTES dele
    assert "nomes_lidos" in bloco, "o nome lido nao aparece junto do botao"
