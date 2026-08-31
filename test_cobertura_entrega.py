"""Cobertura da ENTREGA: a guia so pode faturar se o que foi anexado cobre os
exames que ela autoriza — nao basta existir "algum laudo".

Caso ANA CRISTINA NASCIMENTO DOS SANTOS (GTO 196322294, 18/08, Centro): a guia
autoriza interproximal, panoramica e periapical. O plano tinha UM laudo (o da
panoramica), uma folha ENTREGA e a solicitacao. A guarda `_entregavel_faltando`
pergunta so "existe algum LAUDO_*?" — existia — e a guia faturou sem a panoramica
na folha. A clinica so descobriu conferindo a mao.

Mesma familia do caso PALOMA (GTO 195670786, 31/07, Camacari), que faturou com
dois laudos e ZERO imagem e voltou GLOSADA 3230 "documentacao incompleta"."""
import db
from esteira import (_documentacao_incompleta, _entregavel_faltando,
                     _exames_sem_laudo,
                     _falta_qual_entregavel, _sem_imagem_no_plano)


def test_guia_de_tres_exames_com_laudo_de_um_acusa_os_outros_dois():
    # ANA CRISTINA: so o laudo da panoramica no plano.
    exames = {"interproximal", "panoramica", "periapical"}
    plano = ["ENTREGA_8d63939811.jpg",
             "LAUDO_PANORAMICA_40343617_OFICIAL.pdf",
             "SOLICITACAO_0__WhatsApp_Image_2026-08-18_at_10.48.17.jpeg"]
    assert _exames_sem_laudo(exames, plano) == {"interproximal", "periapical"}


def test_modelo_e_fotografia_nao_exigem_laudo():
    # Regra do dono (22/08): "se o exame e modelo ele nao precisa de laudo; basta
    # uma foto do modelo". Cobrar laudo aqui inventaria pendencia que nao existe.
    exames = {"modelo", "fotografia"}
    plano = ["ENTREGA_041756fe91.jpg", "SOLICITACAO_IVONILDE.jpg"]
    assert _exames_sem_laudo(exames, plano) == set()


def test_documentacao_ortodontica_cobra_panoramica_e_telerradiografia():
    # Regra do dono: documentacao ortodontica SEMPRE inclui panoramica E
    # telerradiografia. A guia pede o procedimento fechado ('documentacao'); os
    # laudos saem com o nome do COMPONENTE. Sem expandir, 'documentacao' nunca
    # acharia laudo nenhum e toda doc orto viraria suspeita.
    exames = {"documentacao"}
    plano = ["LAUDO_PANORAMICA_40338565_OFICIAL.pdf", "SOLICITACAO_PALOMA.jpg"]
    assert _exames_sem_laudo(exames, plano) == {"telerradiografia"}


def test_guia_radiologica_sem_nenhuma_folha_de_imagem_e_suspeita():
    # PALOMA (GTO 195670786, 31/07, Camacari): faturou com dois laudos e a
    # solicitacao, sem UMA imagem. Voltou GLOSADA 3230. Medido em 28/08: so 17 de
    # 853 guias que o robo anexou sairam assim — e anomalia, nao o normal.
    exames = {"documentacao"}
    plano = ["LAUDO_PANORAMICA_40338565_OFICIAL.pdf",
             "LAUDO_TELERRADIOGRAFIA_40338565_CEPH.pdf",
             "SOLICITACAO_Paloma_Maria_Dias_de_Melo_TeixeiraASAS20260731.jpg"]
    assert _sem_imagem_no_plano(exames, plano) is True


def test_guia_com_folha_de_entrega_nao_e_suspeita():
    exames = {"panoramica"}
    plano = ["ENTREGA_82ea2d3457.jpg", "LAUDO_PANORAMICA_40343526_OFICIAL.pdf",
             "SOLICITACAO_1__GISELE_CRISPINA_DOS_SANTOS.jpg"]
    assert _sem_imagem_no_plano(exames, plano) is False


def test_falta_de_laudo_de_periapical_nao_e_sinal_confiavel():
    """Medido em 28/08 sobre as 853 guias que o robo anexou: periapical sai SEM
    laudo em 64% das vezes e interproximal em 81% — nesses dois o laudo e a
    excecao, nao a norma. Panoramica falta em 9% e telerradiografia em 11%.

    Cobrar laudo de periapical/interproximal produziria centenas de pendencias
    falsas — o mesmo erro do gate de imagem por accession, ligado e revertido em
    12/08. Com `apenas_esperados`, so sinaliza o que e anomalia de verdade."""
    exames = {"panoramica", "periapical"}
    plano = ["ENTREGA_82ea2d3457.jpg", "LAUDO_PANORAMICA_40343526_OFICIAL.pdf"]
    assert _exames_sem_laudo(exames, plano) == {"periapical"}
    assert _exames_sem_laudo(exames, plano, apenas_esperados=True) == set()


def test_falta_de_laudo_de_panoramica_continua_sendo_sinal():
    exames = {"panoramica", "periapical"}
    plano = ["ENTREGA_82ea2d3457.jpg", "LAUDO_PERIAPICAL_40336197_OFICIAL.pdf"]
    assert _exames_sem_laudo(exames, plano, apenas_esperados=True) == {"panoramica"}


def test_guia_radiologica_com_laudo_e_sem_imagem_nao_pode_faturar():
    """A guarda que decide anexar aceitava LAUDO sozinho. Foi assim que a PALOMA
    (195670786, 31/07) subiu dois laudos e nenhuma imagem e voltou GLOSADA 3230.

    Guia radiologica precisa dos DOIS: o laudo e a imagem. Medido em 28/08 sobre
    853 guias anexadas — 17 sairam sem imagem (2%), meia guia por dia. Esse e o
    custo de segurar; do outro lado esta a glosa por documentacao incompleta."""
    plano = ["LAUDO_PANORAMICA_40338565_OFICIAL.pdf",
             "LAUDO_TELERRADIOGRAFIA_40338565_CEPH.pdf",
             "SOLICITACAO_Paloma.jpg"]
    assert _entregavel_faltando(False, plano) is True


def test_diz_que_falta_a_IMAGEM_quando_o_laudo_esta_presente():
    """A mensagem tem que mandar a pessoa para o lugar certo. O ramo generico do
    anexador diz "nao ha nenhum laudo para anexar" — no caso PALOMA isso seria
    FALSO e mandaria a clinica cobrar do radiologista um laudo que ja existe."""
    plano = ["LAUDO_PANORAMICA_40338565_OFICIAL.pdf", "SOLICITACAO_Paloma.jpg"]
    assert _falta_qual_entregavel(False, plano) == "imagem"


def test_diz_que_falta_o_LAUDO_quando_so_ha_imagem():
    assert _falta_qual_entregavel(False, ["ENTREGA_ab12cd34ef.jpg"]) == "laudo"


def test_nada_falta_quando_o_par_esta_completo():
    plano = ["ENTREGA_ab12cd34ef.jpg", "LAUDO_PANORAMICA_40342953_OFICIAL.pdf"]
    assert _falta_qual_entregavel(False, plano) == ""


# O motivo chega ao painel com o prefixo que o `salvar_execucao` poe em toda guia
# que tem `anexar_erro`. Sem um grupo proprio, "anexacao falhou" casa primeiro e a
# guia vira NOSSA: sai do painel, entra no loop de retry e vira WhatsApp de falha
# tecnica — re-tentando anexar uma imagem que nao existe. Quem gera a folha e o
# PRORADIS, entao a pendencia e da operacao, nao do robo.
_MOTIVO_SEM_IMAGEM = (
    "Documentação OK, mas a anexação falhou: o laudo está pronto, mas NÃO há "
    "imagem do exame para anexar — guia radiológica precisa das duas coisas, e sem "
    "a imagem a operadora glosa por documentação incompleta (3230). A guia pede "
    "panorâmica. O QUE FAZER: conferir no PRORADIS se a folha de imagens foi gerada "
    "— exame sem template não gera folha — e reprocessar o dia.")


def test_guia_segurada_por_falta_de_imagem_fica_no_painel():
    assert db.eh_pendencia_front(_MOTIVO_SEM_IMAGEM, "auto") is True


def test_guia_segurada_por_falta_de_imagem_nao_entra_no_retry():
    # Re-tentar nao faz o PRORADIS gerar folha. Retry cego so gasta proxy e quota.
    assert db.eh_nosso(_MOTIVO_SEM_IMAGEM, "auto") is False
    assert db.deve_entrar_no_retry(_MOTIVO_SEM_IMAGEM, "auto") is False


def test_guia_segurada_por_falta_de_imagem_tem_grupo_proprio():
    chave, quem, _acao = db.classificar_pendencia(_MOTIVO_SEM_IMAGEM, "auto")
    assert chave == "sem_imagem"
    assert quem == "Radiologista"


def test_guia_de_dois_exames_com_laudo_de_um_so_nao_fatura():
    """Ligar a COBERTURA no portao de escrita. Ate 29/08 a guarda perguntava so
    "existe algum LAUDO_*" — e por isso a guia da CARLANIA (195315958, 23/07), que
    autoriza panoramica e periapical, faturou com o laudo do periapical e sem o da
    panoramica. Foram 71 guias assim na auditoria de 28/08.

    Usa `apenas_esperados`: periapical e interproximal saem sem laudo na maioria
    das vezes (64% e 81%), entao cobrar os dois inventaria pendencia falsa."""
    exames = {"panoramica", "periapical"}
    plano = ["ENTREGA_5333468dd6.jpg",
             "LAUDO_PERIAPICAL BOCA COMPLETA_40335880_OFICIAL.pdf"]
    assert _entregavel_faltando(False, plano) is False   # tem laudo E imagem
    assert _exames_sem_laudo(exames, plano, apenas_esperados=True) == {"panoramica"}


def test_o_inverso_passa():
    exames = {"panoramica", "periapical"}
    plano = ["ENTREGA_5333468dd6.jpg", "LAUDO_PANORAMICA_40335880_OFICIAL.pdf"]
    assert _exames_sem_laudo(exames, plano, apenas_esperados=True) == set()


# ── o portao de escrita: junta entregavel + cobertura ────────────────────────

def test_portao_barra_guia_sem_o_laudo_do_exame_que_ela_autoriza():
    # CARLANIA (195315958): guia pan+peri faturou com o laudo do periapical.
    assert _documentacao_incompleta(
        False, {"panoramica", "periapical"},
        ["ENTREGA_a.jpg", "LAUDO_PERIAPICAL BOCA COMPLETA_1_OFICIAL.pdf"]) == "laudo"


def test_portao_barra_guia_sem_imagem():
    # PALOMA (195670786): dois laudos, nenhuma imagem -> GLOSADA 3230.
    assert _documentacao_incompleta(
        False, {"documentacao"},
        ["LAUDO_PANORAMICA_1_OFICIAL.pdf", "LAUDO_TELERRADIOGRAFIA_1_CEPH.pdf"]) == "imagem"


def test_portao_libera_quando_esta_completo():
    assert _documentacao_incompleta(
        False, {"panoramica", "periapical"},
        ["ENTREGA_a.jpg", "LAUDO_PANORAMICA_1_OFICIAL.pdf"]) == ""


def test_portao_nao_cobra_laudo_de_periapical_nem_de_interproximal():
    """Periapical sai sem laudo em 64% das guias e interproximal em 81%: exigir os
    dois inventaria pendencia falsa. O laudo da PANORAMICA basta para a guia toda.
    (Guia radiologica sem laudo NENHUM continua barrada — regra anterior.)"""
    assert _documentacao_incompleta(
        False, {"panoramica", "periapical", "interproximal"},
        ["ENTREGA_a.jpg", "LAUDO_PANORAMICA_1_OFICIAL.pdf"]) == ""


def test_portao_de_guia_de_modelo_exige_so_a_foto():
    assert _documentacao_incompleta(True, {"modelo"}, ["ENTREGA_a.jpg"]) == ""
    assert _documentacao_incompleta(True, {"modelo"}, ["SOLICITACAO_a.pdf"]) == "imagem"


# ── roteamento da pendencia de COBERTURA ────────────────────────────────────
# O `salvar_execucao` prefixa toda guia com `anexar_erro` com "Documentacao OK, mas
# a anexacao falhou:". Com esse prefixo o motivo casava primeiro no grupo `anexacao`
# -> responsavel "Nos" -> saia do painel e entrava no retry. Aconteceu de verdade:
# em 31/08 as guias de REBECA (196718705), LUCCA (196712279) e DEISIANE (196691026),
# corretamente barradas por falta do laudo da panoramica, ficaram invisiveis para a
# clinica e queimando re-tentativa. Mesmo erro que a mensagem de imagem teve em
# 28/08 — e que eu nao repliquei aqui.

_MOTIVO_SEM_COBERTURA = (
    "Documentação OK, mas a anexação falhou: a guia autoriza documentação "
    "ortodôntica e periapical, mas NÃO há o laudo de panorâmica entre os documentos "
    "— faturar assim entrega menos do que a guia autoriza e a operadora glosa por "
    "documentação incompleta (3230). O QUE FAZER: cobrar a emissão desse laudo; o "
    "robô anexa sozinho assim que ele sair no PRORADIS.")


def test_falta_de_laudo_do_exame_fica_no_painel():
    assert db.eh_pendencia_front(_MOTIVO_SEM_COBERTURA, "auto") is True


def test_falta_de_laudo_do_exame_nao_entra_no_retry():
    """Re-tentar nao faz o radiologista assinar. Retry cego so gasta proxy."""
    assert db.eh_nosso(_MOTIVO_SEM_COBERTURA, "auto") is False
    assert db.deve_entrar_no_retry(_MOTIVO_SEM_COBERTURA, "auto") is False


def test_falta_de_laudo_do_exame_e_do_radiologista():
    chave, quem, _acao = db.classificar_pendencia(_MOTIVO_SEM_COBERTURA, "auto")
    assert chave == "sem_laudo_do_exame"
    assert quem == "Radiologista"
