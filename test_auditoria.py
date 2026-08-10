"""Testes da Camada 2 — auditoria de upload (auditoria_upload.py).

Veredito PURO: dado (o que o robô diz que anexou, o que está de fato na guia),
dizer se o upload está OK, PARCIAL (algo sumiu) ou AUSENTE (nada nosso na guia).
O driver do portal (só-leitura) não é testado aqui — só a lógica de veredito.
"""
from auditoria_upload import _tipos_nossos, auditar_guia


def test_tipos_reconhece_so_os_nossos_arquivos():
    nomes = ["SOLICITACAO_0__maria.jpg", "LAUDO_PANORAMICA_40336879_OFICIAL.pdf",
             "ENTREGA_abc123def0.jpg", "img_ASSINADA.png", "foto do celular.jpg"]
    # img_ASSINADA (cópia automática da GTO) e a foto manual NÃO contam como nossos
    assert _tipos_nossos(nomes) == {"solic", "laudo", "img"}


def test_laudo_ceph_tambem_conta_como_laudo():
    assert _tipos_nossos(["LAUDO_TELERRADIOGRAFIA_999_CEPH.pdf"]) == {"laudo"}


def test_guia_ok_quando_tudo_que_anexamos_esta_la():
    esperados = ["LAUDO_PANORAMICA_111_OFICIAL.pdf", "SOLICITACAO_ana.jpg",
                 "ENTREGA_aaaa11bb.jpg"]
    portal = esperados + ["img_ASSINADA.png"]      # + cópia da GTO, irrelevante
    r = auditar_guia(esperados, portal)
    assert r["status"] == "OK"
    assert r["faltando"] == []


def test_guia_ausente_quando_nada_nosso_chegou_na_guia():
    # o robô registrou laudo+solicitação, mas a guia só tem a cópia da GTO e lixo manual
    esperados = ["LAUDO_X_1_OFICIAL.pdf", "SOLICITACAO_x.jpg"]
    portal = ["img_ASSINADA.png", "Captura de tela.jpg"]
    r = auditar_guia(esperados, portal)
    assert r["status"] == "AUSENTE"
    assert set(r["faltando"]) == {"laudo", "solic"}


def test_guia_parcial_quando_o_laudo_sumiu():
    esperados = ["LAUDO_X_1_OFICIAL.pdf", "SOLICITACAO_x.jpg"]
    portal = ["SOLICITACAO_x.jpg", "img_ASSINADA.png"]
    r = auditar_guia(esperados, portal)
    assert r["status"] == "PARCIAL"
    assert r["faltando"] == ["laudo"]


def test_sem_plano_mas_com_nossos_arquivos_e_ok():
    # execução antiga sem arquivos_plano no banco, porém a guia TEM nossos arquivos
    r = auditar_guia([], ["LAUDO_X_1_OFICIAL.pdf", "ENTREGA_aa11bb.jpg"])
    assert r["status"] == "OK"


def test_sem_plano_e_sem_nossos_arquivos_fica_sem_plano():
    # não dá para afirmar nada: o banco não registrou o que anexou e não há
    # arquivo nosso na guia -> marca SEM_PLANO (revisar à mão), não AUSENTE
    r = auditar_guia([], ["img_ASSINADA.png"])
    assert r["status"] == "SEM_PLANO"
