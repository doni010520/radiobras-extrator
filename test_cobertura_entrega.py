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
from esteira import (_entregavel_faltando, _exames_sem_laudo,
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
