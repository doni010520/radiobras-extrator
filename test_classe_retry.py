"""Fase 0 — taxonomia de retry: classe_retry(motivo, categoria) diz se a falha é
`transitorio` (infra: retenta sozinho), `logica` (precisa conserto/leitura) ou
`externo` (esperando clínica/radiologista/cadastro -> pendência, sem retry).

Regra: o LOOP só retenta `transitorio`. Externo NUNCA. Lógica NÃO é retry cego."""
from db import classe_retry


def test_gemini_e_rede_sao_transitorio():
    for m in ("gemini: 503 UNAVAILABLE", "gemini: 500 timeout",
              "net::ERR_INTERNET_DISCONNECTED", "Execution context was destroyed",
              "ERR_TUNNEL_CONNECTION_FAILED", "could not translate host name",
              "Timeout 30000ms exceeded", "TE-BFF-GTO-0001 500"):
        assert classe_retry(m) == "transitorio", m


def test_falha_tecnica_de_leitura_e_transitorio():
    # ALESSANDRA: "falha técnica na leitura dos documentos" — o nascimento agora tem
    # retry, entao re-rodar recupera. Homonimo tambem cai aqui (nascimento desempata).
    assert classe_retry("NÃO FATUROU por falha técnica na leitura dos documentos") == "transitorio"
    assert classe_retry("2 pacientes com o nome 'ALESSANDRA' no PRORADIS — não foi possível") == "transitorio"


def test_esperando_externo_nunca_retenta():
    for m in ("sem laudo pronto ainda",                       # falta_laudo
              "falta o LAUDO da telerradiografia (traçado)",   # esperando_tele
              "não há nenhum pedido do dentista anexado ao prontuário",  # sem_pedido
              "paciente não foi encontrado no PRORADIS",       # cadastro
              "o pedido do dentista não cobre tudo que a guia autoriza"):  # nao_cobre
        assert classe_retry(m) == "externo", m


def test_logica_precisa_conserto_ou_leitura():
    # nome não bate (mãe/responsável), pedido ilegível, revisão humana -> lógica
    assert classe_retry("nenhum documento do prontuário está no nome deste paciente") == "logica"
    assert classe_retry("a caligrafia do pedido está ilegível") == "logica"
    assert classe_retry("revisão humana") == "logica"


def test_homonimo_mesmo_dia_e_conferencia_nao_nosso():
    # 195904169 (dry-run 13/08): "mais de um paciente no mesmo dia" (lado analítico)
    # NÃO se resolve com retry — precisa de humano -> Conferência (vai pro FRONT),
    # não 'nosso'. A ALESSANDRA ("2 pacientes com o nome ... não foi possível") segue
    # transitório (o nascimento desempata num re-run).
    from db import eh_pendencia_front, classificar_pendencia
    m = ("NÃO FATUROU porque há mais de um paciente com esse nome no PRORADIS no "
         "mesmo dia, e o sistema não tem como saber qual é o certo.")
    assert classe_retry(m) != "transitorio"
    assert eh_pendencia_front(m, "revisao") is True
    assert classificar_pendencia(m)[1] == "Conferência"
    # a ALESSANDRA NÃO regride: continua transitório
    assert classe_retry("2 pacientes com o nome 'ALESSANDRA' no PRORADIS — não foi possível") == "transitorio"


def test_vazio_ou_desconhecido_nao_e_transitorio():
    # sem sinal claro NÃO vira transitorio (não retenta cegamente algo que não é infra)
    assert classe_retry("") != "transitorio"
    assert classe_retry("motivo qualquer sem padrão") != "transitorio"
