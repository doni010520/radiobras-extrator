"""A guia JA_ANEXADO incompleta tem de entrar na fila de completar, nao so virar
mensagem.

Caso ANA PAULA VIEIRA DIAS (196933166). Em 05/09 11:15 o robo anexou e perdeu dois
dos tres arquivos no caminho (ver test_upload_parcial_ana_paula). A guia ficou com
2 anexos. A partir dai a descoberta passou a responder "ja tem documentacao, pula"
— porque a trava anti-duplicacao recusa escrever em guia com documento — e o robo
voltou nela SEIS vezes, em 05, 06, 07 e 08/09, repetindo "nao ha imagem do exame"
sem poder fazer nada. Foi resolvida a mao em 08/09 09:24, no penultimo dia do prazo.

A trava esta certa como padrao: o OdontoPrev nao permite remover anexo. Mas ela
estava cobrindo um caso que nao e dela — a guia com documento, com falta PROVADA
do que ela autoriza, e com o arquivo disponivel. Isso nao e "alguem ja fez"; e uma
guia incompleta que da para completar, que e exatamente o que `completar.py` faz
(so dentro dos 7 dias, so o exame comprovadamente ausente, `max_antes` vindo da
contagem viva da API).

O que faltava era ligar os dois: `faturadas_desde` so devolvia o que o ROBO anexou
(`auto`/`justificativa`), entao a guia travada nunca chegava na conferencia."""
from conferencia import conferivel


def test_ja_anexada_incompleta_entra_na_conferencia():
    """O caso ANA PAULA: categoria ja_anexada e faturado=False (falta imagem)."""
    assert conferivel({"categoria": "ja_anexada", "faturado": False}) is True


def test_ja_anexada_completa_nao_precisa_de_nada():
    """Documentacao da clinica que cobre a guia: nao gastar chamada nem risco."""
    assert conferivel({"categoria": "ja_anexada", "faturado": True}) is False


def test_guia_que_o_robo_faturou_continua_sendo_conferida():
    assert conferivel({"categoria": "auto", "faturado": True}) is True
    assert conferivel({"categoria": "justificativa", "faturado": True}) is True


def test_pendencia_de_verdade_fica_de_fora():
    """Guia sem laudo/sem pedido nao tem o que conferir no portal — e pendencia."""
    assert conferivel({"categoria": "sem_laudo", "faturado": False}) is False
    assert conferivel({"categoria": "sem_solicitacao", "faturado": False}) is False
    assert conferivel({"categoria": "sem_exame", "faturado": False}) is False


def test_auto_que_nao_faturou_fica_de_fora():
    """`auto` sem faturar e anexacao que falhou — volta pela esteira, nao aqui."""
    assert conferivel({"categoria": "auto", "faturado": False}) is False


def test_linha_sem_os_campos_nao_estoura():
    assert conferivel({}) is False
    assert conferivel({"categoria": None, "faturado": None}) is False
