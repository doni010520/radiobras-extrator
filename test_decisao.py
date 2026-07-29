"""
test_decisao.py — Trava as regras de DECISÃO do faturamento.

Só funções puras (sem rede, sem navegador, sem Gemini): o que decide se uma guia
é faturada ou vira pendência. Cada teste nomeia o caso REAL que o originou —
quem mexer nessas regras vê o que quebra e por quê.

    pytest test_decisao.py -q
"""
import os

import pytest

from esteira import (_acc_do_laudo, _escolher_solicitacao, _filtrar_arquivos_da_gto,
                     _nomes_compat, alvo_cobertura)
from solicitacao_utils import canon_exames, expande_documentacao


def _leitura(idx, paciente, exames, tipo="solicitacao", legivel=True):
    return {"idx": idx, "tipo": tipo, "legivel": legivel,
            "paciente_lido": paciente, "exames_lidos": exames}


# ── Alvo de cobertura (Bug 1) ────────────────────────────────────────────────
# Prontuário com DUAS guias (uma de panorâmica, outra de interproximal). Ao
# faturar a da panorâmica, a união exigia interproximal também: guia correta
# virava pendência, com a mensagem "pede [panoramica] mas a GTO pede [panoramica]"
# — idênticas, porque o critério usava a união e a mensagem usava a guia certa.
# Log de 22/07 Centro.

def test_alvo_prefere_a_guia_desta_gto():
    alvo = alvo_cobertura({"panoramica"}, ["interproximal"], {"panoramica", "interproximal"})
    assert alvo == {"panoramica"}


def test_alvo_cai_no_portal_quando_a_gto_nao_foi_identificada():
    alvo = alvo_cobertura(set(), ["Panoramica"], {"panoramica", "interproximal"})
    assert alvo == {"Panoramica"}


def test_alvo_so_usa_a_uniao_em_ultimo_caso():
    alvo = alvo_cobertura(set(), [], {"panoramica", "interproximal"})
    assert alvo == {"panoramica", "interproximal"}


def test_solicitacao_da_guia_certa_e_aceita():
    """Com o alvo correto, a solicitação de panorâmica passa."""
    leituras = [_leitura(0, "MARIA DA SILVA SANTOS", ["panoramica"])]
    idx, _a, motivo = _escolher_solicitacao(leituras, "MARIA DA SILVA SANTOS",
                                            {"panoramica"}, 1)
    assert idx == 0 and motivo is None


def test_a_uniao_reprovaria_a_mesma_solicitacao():
    """Documenta o bug: com a união como alvo, a MESMA leitura é reprovada."""
    leituras = [_leitura(0, "MARIA DA SILVA SANTOS", ["panoramica"])]
    idx, _a, motivo = _escolher_solicitacao(leituras, "MARIA DA SILVA SANTOS",
                                            {"panoramica", "interproximal"}, 1)
    assert idx is None
    assert motivo == "NAO_COBRE"


# ── Documentação x componentes (Bug 2) ───────────────────────────────────────
# GTO pede "Doc Orto Compl" -> {documentacao}. A solicitação escreve os
# componentes. O issubset falhava e reprovava pedido CORRETO e mais completo que
# a exigência. Log de 21/07 Camaçari.

def test_componentes_cobrem_uma_gto_de_documentacao():
    lido = canon_exames("panoramica telerradiografia fotografias modelos "
                        "periapicais oclusal")
    assert "documentacao" in expande_documentacao(lido)
    assert {"documentacao", "periapical"}.issubset(expande_documentacao(lido))


def test_caso_real_camacari_passa_no_escolher():
    leituras = [_leitura(0, "JOAO PEDRO ALVES", ["panoramica", "telerradiografia",
                                                 "fotografias", "modelos", "periapicais"])]
    idx, _a, motivo = _escolher_solicitacao(leituras, "JOAO PEDRO ALVES",
                                            {"documentacao", "periapical"}, 1)
    assert idx == 0 and motivo is None


def test_sem_ancora_nao_vira_documentacao():
    """{periapical, oclusal, fotografia} são 3 componentes, mas não é documentação:
    faltam panorâmica e telerradiografia. Contar componentes sem âncora aceitaria."""
    assert "documentacao" not in expande_documentacao(
        {"periapical", "oclusal", "fotografia"})


def test_poucos_componentes_nao_viram_documentacao():
    assert "documentacao" not in expande_documentacao({"panoramica", "periapical"})


def test_expansao_nao_vale_na_direcao_inversa():
    """Pedir 'documentação' NÃO satisfaz uma guia que exige panorâmica avulsa —
    a expansão só pode ser aplicada ao lado da SOLICITAÇÃO."""
    assert "panoramica" not in expande_documentacao({"documentacao"})
    leituras = [_leitura(0, "ANA LIMA COSTA", ["documentacao"])]
    idx, _a, _m = _escolher_solicitacao(leituras, "ANA LIMA COSTA", {"panoramica"}, 1)
    assert idx is None


# ── Identidade do paciente (regressão das guardas já existentes) ─────────────

def test_irmao_nao_casa():
    assert not _nomes_compat("PEDRO SILVA SANTOS", "JOAO SILVA SANTOS")


def test_erro_de_grafia_casa():
    assert _nomes_compat("IONICE ALVES PEREIRA", "JONICE ALVES PEREIRA")


def test_pai_e_filho_nao_casam_por_um_token():
    assert not _nomes_compat("JOSE CARLOS SOUZA LIMA", "JOSE CARLOS SOUZA JUNIOR")


# ── Exame particular / procedência do accession (item 3) ────────────────────

def _pasta(tmp_path, nomes):
    for n in nomes:
        (tmp_path / n).write_bytes(b"x")
    return str(tmp_path)


def test_acc_do_laudo():
    assert _acc_do_laudo("LAUDO_PANORAMICA_12345_OFICIAL.pdf") == "12345"
    assert _acc_do_laudo("ENTREGA_1.jpg") is None


def test_particular_sai_pela_procedencia_e_a_solicitacao_fica(tmp_path):
    """Exame particular (accession fora do analítico) não vai pro convênio.
    A SOLICITAÇÃO tem de continuar subindo — sem ela a guia era anexada só com o
    laudo, sem o documento que a própria decisão exigiu, e dada como faturada."""
    pasta = _pasta(tmp_path, ["LAUDO_PANORAMICA_111_OFICIAL.pdf",
                              "LAUDO_TOMOGRAFIA_999_OFICIAL.pdf",
                              "ENTREGA_1.jpg",
                              "SOLICITACAO_pedido.pdf"])
    arquivos, excluidos, _fora = _filtrar_arquivos_da_gto(
        pasta, {"gto_exames_desta": ["panoramica"]}, extras_acc=["999"])
    base = sorted(os.path.basename(a) for a in arquivos)
    assert base == ["LAUDO_PANORAMICA_111_OFICIAL.pdf", "SOLICITACAO_pedido.pdf"]
    assert "LAUDO_TOMOGRAFIA_999_OFICIAL.pdf" in excluidos
    assert "ENTREGA_1.jpg" in excluidos      # não atribuível a um exame


def test_sem_misto_sobe_a_pasta_inteira(tmp_path):
    pasta = _pasta(tmp_path, ["LAUDO_PANORAMICA_111_OFICIAL.pdf", "ENTREGA_1.jpg",
                              "SOLICITACAO_pedido.pdf"])
    arquivos, excluidos, _f = _filtrar_arquivos_da_gto(
        pasta, {"gto_exames_desta": ["panoramica"]}, extras_acc=["999"])
    assert len(arquivos) == 3 and excluidos == []


def test_gto_ilegivel_nao_filtra_nada(tmp_path):
    """Sem exames de referência E sem procedência, não dá pra decidir: mantém tudo
    (comportamento antigo — a guia vira pendência por outro caminho)."""
    pasta = _pasta(tmp_path, ["LAUDO_PANORAMICA_111_OFICIAL.pdf", "ENTREGA_1.jpg"])
    arquivos, excluidos, _f = _filtrar_arquivos_da_gto(pasta, {}, extras_acc=None)
    assert len(arquivos) == 2 and excluidos == []


def test_fallback_pelo_nome_do_exame_continua_valendo(tmp_path):
    """Sem procedência (fallback por nome, cod 'WL*'), a heurística antiga segue
    protegendo: laudo de exame fora da guia não sobe."""
    pasta = _pasta(tmp_path, ["LAUDO_PANORAMICA_111_OFICIAL.pdf",
                              "LAUDO_TOMOGRAFIA_999_OFICIAL.pdf",
                              "SOLICITACAO_pedido.pdf"])
    arquivos, excluidos, fora = _filtrar_arquivos_da_gto(
        pasta, {"gto_exames_desta": ["panoramica"]}, extras_acc=None)
    base = sorted(os.path.basename(a) for a in arquivos)
    assert base == ["LAUDO_PANORAMICA_111_OFICIAL.pdf", "SOLICITACAO_pedido.pdf"]
    assert fora == ["tomografia"]


# ── Trava de concorrência (item 2) ───────────────────────────────────────────
# A versão anterior perguntava a `_esteira_jobs` se o dono da reserva estava vivo.
# Só o /faturar/run registra a tag lá: para o cron ('cron-<conta>-<dia>') e para o
# /fechar (job_id em `_jobs`), `job` era None e a reserva alheia era SOBRESCRITA —
# o cenário real (cron às 5h + clique em Faturar) passava direto.

def test_trava_bloqueia_segunda_execucao():
    import app
    app._esteira_ativas.clear()
    assert app._esteira_reservar("24/07/2026", "388336", "jid-1") == "jid-1"
    assert app._esteira_reservar("24/07/2026", "388336", "jid-2") is None


def test_trava_bloqueia_tag_de_cron_nao_registrada_em_jobs():
    """A regressão exata: tag do cron não existe em _esteira_jobs."""
    import app
    app._esteira_ativas.clear()
    assert app._esteira_reservar("24/07/2026", "388336", "cron-388336-24/07/2026")
    assert "cron-388336-24/07/2026" not in app._esteira_jobs      # nunca esteve lá
    assert app._esteira_reservar("24/07/2026", "388336", "jid-web") is None


def test_trava_nao_bloqueia_outra_unidade():
    import app
    app._esteira_ativas.clear()
    assert app._esteira_reservar("24/07/2026", "388336", "a")
    assert app._esteira_reservar("24/07/2026", "410923", "b") == "b"


def test_trava_libera_e_expira():
    import app
    app._esteira_ativas.clear()
    app._esteira_reservar("24/07/2026", "388336", "a")
    app._esteira_liberar("24/07/2026", "388336", "outra")   # tag errada: não libera
    assert app._esteira_reservar("24/07/2026", "388336", "b") is None
    app._esteira_liberar("24/07/2026", "388336", "a")
    assert app._esteira_reservar("24/07/2026", "388336", "c") == "c"
    # reserva órfã (processo morto) não trava o dia pra sempre
    assert app._esteira_reservar("24/07/2026", "388336", "d", ttl=0) == "d"


def test_rotas_admin_esteira_nao_existem_mais():
    import app
    rotas = {str(r) for r in app.app.url_map.iter_rules()}
    assert not [r for r in rotas if "admin/esteira" in r]


# ── Cifra da senha do portal (item 7) ────────────────────────────────────────

def test_senha_do_portal_cifra_e_decifra(monkeypatch):
    from cryptography.fernet import Fernet
    import db
    monkeypatch.setenv("PORTAL_KEY", Fernet.generate_key().decode())
    guardado = db._cifrar("senha-secreta")
    assert guardado.startswith("enc:") and "senha-secreta" not in guardado
    assert db._decifrar(guardado) == "senha-secreta"


def test_senha_legado_em_texto_puro_continua_lendo(monkeypatch):
    """Migração sem downtime: o que já está no banco em claro segue funcionando."""
    import db
    monkeypatch.delenv("PORTAL_KEY", raising=False)
    assert db._decifrar("senha-antiga") == "senha-antiga"
    assert db._cifrar("x") == "x"          # sem chave, comportamento de hoje


# ── Guia de documentação x laudos dos componentes (achado do DRY 20/07) ──────
# GTO pede 'documentacao'; os laudos chegam como LAUDO_PANORAMICA e
# LAUDO_TELERRADIOGRAFIA — que SÃO a documentação. O filtro lia 'panoramica', não
# achava na guia e descartava como exame particular: a guia subia SEM LAUDO (casos
# DARLAN/ROSEANGELA) ou com ZERO arquivo (JOEL), e ainda assim contava como
# faturada. Mesmo desencontro do Bug 2, do lado do laudo.

def test_guia_de_documentacao_aceita_laudo_dos_componentes(tmp_path):
    from solicitacao_utils import componentes_da_documentacao
    assert {"panoramica", "telerradiografia"} <= componentes_da_documentacao({"documentacao"})
    pasta = _pasta(tmp_path, ["LAUDO_PANORAMICA_40334886_OFICIAL.pdf",
                              "LAUDO_TELERRADIOGRAFIA_40334886_CEPH.pdf",
                              "ENTREGA_1.jpg", "SOLICITACAO_darlan.jpg"])
    arquivos, excluidos, _f = _filtrar_arquivos_da_gto(
        pasta, {"gto_exames_desta": ["documentacao", "periapical"]}, extras_acc=None)
    assert len(arquivos) == 4 and excluidos == []


def test_documentacao_nao_abre_a_porta_pra_qualquer_exame(tmp_path):
    """A expansão cobre os componentes, não tudo: tomografia continua de fora."""
    pasta = _pasta(tmp_path, ["LAUDO_PANORAMICA_111_OFICIAL.pdf",
                              "LAUDO_TOMOGRAFIA_999_OFICIAL.pdf"])
    arquivos, excluidos, fora = _filtrar_arquivos_da_gto(
        pasta, {"gto_exames_desta": ["documentacao"]}, extras_acc=None)
    assert [os.path.basename(a) for a in arquivos] == ["LAUDO_PANORAMICA_111_OFICIAL.pdf"]
    assert fora == ["tomografia"]


def test_upload_de_lista_vazia_nao_e_sucesso():
    """'Nada para enviar' != 'tudo anexado'. A lista vazia caía no return da
    idempotência com ok=True e a guia era registrada como FATURADA sem ter subido
    arquivo nenhum (caso JOEL, 20/07)."""
    from extrator_odontoprev import upload_arquivos

    class _GPFake:
        def inner_text(self, _sel): return "total de anexos) : 0"
        def query_selector(self, _sel): return None
        def wait_for_timeout(self, _ms): pass
        def query_selector_all(self, _sel): return []

    r = upload_arquivos(_GPFake(), [])
    assert r["ok"] is False and r["enviados"] == []


# ── Nome composto grudado x separado (caso VERALUCIA, 22/07 Camaçari) ────────
# A guia traz "VERALUCIA" num token; o pedido do dentista traz "Vera Lucia" em
# dois. Token a token nunca fecha: a distância de VERALUCIA para VERA é 3 e o teto
# de erro de grafia é 2. Concatenar PRESERVA o nome, então é seguro.

def test_nome_composto_grudado_casa_com_separado():
    assert _nomes_compat("VERA LUCIA SOUSA DOS SANTOS", "VERALUCIA SOUSA DOS SANTOS")
    assert _nomes_compat("VERALUCIA SOUSA DOS SANTOS", "VERA LUCIA SOUSA DOS SANTOS")
    assert _nomes_compat("ANA MARIA DA SILVA COSTA", "ANAMARIA DA SILVA COSTA")


def test_concatenacao_nao_deixa_passar_outra_pessoa():
    """A porta aberta é só para a MESMA sequência de letras sem o espaço."""
    assert not _nomes_compat("MARIA JOSE SILVA SANTOS", "MARIA HELENA SILVA SANTOS")
    assert not _nomes_compat("ANA CLARA SOUZA LIMA", "ANA BEATRIZ SOUZA LIMA")
    assert not _nomes_compat("PEDRO SILVA SANTOS", "JOAO SILVA SANTOS")


# ── Vocabulário de exames (caso MIRLA, 22/07 Centro) ────────────────────────
# GTO: "Doc Orto Compl". Pedido manuscrito: "Teleradigrafia lateral com tweed e
# Usp / Fotos intra e extras bucais / Panorâmica em topo / Modelo fisicos".
# 'Teleradigrafia' não casava `telerr` e 'Fotos' não casava `fotograf`: sem as duas
# âncoras, a documentação era reprovada e a guia virava pendência.

MIRLA = ("Teleradigrafia lateral com tweed e Usp. Fotos intra e extras bucais. "
         "Panoramica em topo. Modelo fisicos")


def test_caso_mirla_cobre_a_documentacao():
    lido = expande_documentacao(canon_exames(MIRLA))
    assert {"telerradiografia", "fotografia", "panoramica", "modelo"} <= lido
    assert "documentacao" in lido


def test_mirla_passa_no_escolher():
    leituras = [_leitura(0, "MIRLA CHRISTINE TEIXEIRA DE OLIVEIRA",
                         [MIRLA])]
    idx, _a, motivo = _escolher_solicitacao(
        leituras, "MIRLA CHRISTINE TEIXEIRA DE OLIVEIRA", {"documentacao"}, 1)
    assert idx == 0 and motivo is None


def test_grafia_errada_de_exame_e_tolerada():
    assert "telerradiografia" in canon_exames("teleradigrafia")
    assert "panoramica" in canon_exames("panoramicaa")


def test_fuzzy_nao_inventa_exame():
    """Reconhecer exame que não foi pedido faz a solicitação 'cobrir' o que ela não
    cobre — e aí o sistema fatura errado. Palavra genérica não pode virar exame."""
    for t in ["etc", "raio x", "radiografia", "documento", "consulta odontologica"]:
        assert canon_exames(t) == set(), t


def test_tomografia_e_fotografia_nao_se_confundem():
    """Estão a 2 letras uma da outra. Com teto 2 colidiriam — exame caro virando
    foto. O teto é 1 justamente por isto."""
    assert canon_exames("tomografia") == {"tomografia"}
    assert canon_exames("fotografia") == {"fotografia"}


# ── Anexos que o Gemini não lê direto (casos ALESSANDRA e JANDIARA) ─────────
# A solicitação estava em .tif — saída padrão de scanner. O código só aceitava
# pdf/png/jpg/jpeg e descartava o resto SEM LOG: a guia virava "nenhum documento
# com nome compatível", quando o documento existia e nunca tinha sido olhado.

def test_tif_e_convertido_em_vez_de_descartado():
    from esteira import preparar_anexo
    from PIL import Image
    import io as _io
    buf = _io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="TIFF")
    mime, blob = preparar_anexo("SOLICITACAO.tif", buf.getvalue())
    assert mime == "image/jpeg"
    assert blob[:2] == b"\xff\xd8"          # virou JPEG de verdade


def test_formatos_diretos_passam_intactos():
    from esteira import preparar_anexo
    mime, blob = preparar_anexo("pedido.pdf", b"%PDF-1.4 x")
    assert mime == "application/pdf" and blob == b"%PDF-1.4 x"


def test_formato_desconhecido_devolve_motivo():
    """Não pode mais sumir em silêncio: quem descarta explica por quê."""
    mime, motivo = preparar_anexo_seguro("video.mp4", b"\x00\x00")
    assert mime is None and "mp4" in motivo


def preparar_anexo_seguro(nome, blob):
    from esteira import preparar_anexo
    return preparar_anexo(nome, blob)


def test_doc_orto_exige_os_quatro_exames():
    """Regra do dono (28/07, corrigida): a Doc Orto COMPLETA é telerradiografia +
    fotos + modelos. NÃO inclui panorâmica. A Doc Orto CONTROLE é fotos +
    panorâmica, somente."""
    from solicitacao_utils import _DOC_ORTO, _DOC_CONTROLE
    assert _DOC_ORTO == {"telerradiografia", "fotografia", "modelo"}
    assert _DOC_CONTROLE == {"fotografia", "panoramica"}
    assert "panoramica" not in _DOC_ORTO
    assert "documentacao_completa" in expande_documentacao(set(_DOC_ORTO))
    # Faltando qualquer um dos quatro, deixa de ser COMPLETA. Pode continuar
    # servindo para um subtipo menor (Controle) — por isso o alvo é o token
    # 'documentacao_completa', não o genérico.
    for faltando in _DOC_ORTO:
        parcial = set(_DOC_ORTO) - {faltando}
        assert "documentacao_completa" not in expande_documentacao(parcial), faltando


def test_extras_nao_atrapalham_a_doc_orto():
    from solicitacao_utils import _DOC_ORTO
    lido = set(_DOC_ORTO) | {"periapical", "oclusal"}
    assert "documentacao" in expande_documentacao(lido)


def test_box_invalido_nao_derruba_a_guia():
    """O modelo às vezes devolve uma lista de caixas; o desempacotamento estourava
    e a guia virava pendência com a documentação correta (ALANNA, 22/07)."""
    from esteira import _box4
    assert _box4([10, 20, 30, 40]) == [10.0, 20.0, 30.0, 40.0]
    assert _box4([[10, 20, 30, 40]]) == [10.0, 20.0, 30.0, 40.0]
    assert _box4([10, 20, 30, 40, 50]) is None
    assert _box4([[1, 2, 3, 4], [5, 6, 7, 8]]) is None
    assert _box4(None) is None and _box4("x") is None


# ── Subtipo da documentação (HAMILTON 195268018, 22/07) ─────────────────────
# "Doc Orto Compl" e "Doc Orto Contro" são procedimentos DIFERENTES. A regra dos
# quatro exames vale para a COMPLETA; aplicá-la ao CONTROLE reprovava guia legítima.

def test_completa_e_controle_sao_procedimentos_diferentes():
    assert "documentacao_completa" in canon_exames("Doc Orto Compl")
    assert "documentacao_completa" not in canon_exames("Doc Orto Contro")
    assert "documentacao" in canon_exames("Doc Orto Contro")


def test_so_os_quatro_cobrem_a_doc_completa():
    sem_modelo = expande_documentacao({"panoramica", "telerradiografia",
                                       "fotografia", "periapical", "oclusal"})
    assert not canon_exames("Doc Orto Compl") <= sem_modelo   # falta modelo
    assert canon_exames("Doc Orto Contro") <= sem_modelo      # foto+panoramica: cobre


def test_doc_completa_nao_cobre_automaticamente_o_controle():
    """São composições diferentes: a completa não tem panorâmica, o controle tem."""
    from solicitacao_utils import _DOC_ORTO, _DOC_CONTROLE
    assert canon_exames("Doc Orto Compl") <= expande_documentacao(set(_DOC_ORTO))
    assert canon_exames("Doc Orto Contro") <= expande_documentacao(set(_DOC_CONTROLE))


def test_motivo_diz_o_que_falta_e_nao_vaza_token_interno():
    """A operadora precisa saber QUAL exame falta no pedido — e 'documentacao_completa'
    é token interno, nao pode aparecer na mensagem."""
    leituras = [_leitura(0, "ANDNA JAIRA NEVES", ["panoramica", "telerradiografia",
                                                 "fotografias", "periapicais", "oclusal"])]
    alvo = canon_exames("Doc Orto Compl") | {"periapical"}
    idx, _a, motivo = _escolher_solicitacao(leituras, "ANDNA JAIRA NEVES", alvo, 1)
    assert idx is None
    assert motivo == "NAO_COBRE"
    from solicitacao_utils import lista_amigavel
    # o token interno nunca aparece no texto que a operadora le
    assert "documentacao_completa" not in lista_amigavel(alvo)


def test_diag_enxerga_a_esteira_rodando():
    """O /api/diag contava só `_jobs` e dizia '0 jobs ativos' com a esteira
    faturando. É por ele que se decide se pode deployar — e deploy no meio de uma
    execução mata o job."""
    import app
    app._esteira_jobs.clear(); app._esteira_ativas.clear()
    with app.app.test_request_context():
        app._esteira_jobs["j1"] = {"done": False, "dia": "22/07/2026",
                                   "conta": "388336", "dry": True}
        ativos = [k for k, j in app._esteira_jobs.items() if not j.get("done")]
        assert ativos == ["j1"]
        app._esteira_jobs["j1"]["done"] = True
        assert not [k for k, j in app._esteira_jobs.items() if not j.get("done")]
    app._esteira_jobs.clear()


# ── Falha fatal do Gemini nao pode arrastar a execucao (28/07) ───────────────
# A usuaria relatou uma execucao de 20 MINUTOS. Causa: cada GTO tentava 3x e cada
# tentativa reenviava ate 15 documentos antes de levar o 429 de credito esgotado.

def test_erro_de_credito_e_reconhecido_como_fatal():
    from esteira import _gem_fatal, _gem_estado
    _gem_estado["fatal"] = None
    assert _gem_fatal("429 RESOURCE_EXHAUSTED. Your prepayment credits ran out")
    assert _gem_estado["fatal"]
    _gem_estado["fatal"] = None
    assert _gem_fatal("PERMISSION_DENIED: API key invalid")
    _gem_estado["fatal"] = None


def test_erro_temporario_nao_e_fatal():
    """Timeout ou erro de rede DEVE continuar tentando — só crédito/chave para."""
    from esteira import _gem_fatal, _gem_estado
    _gem_estado["fatal"] = None
    assert not _gem_fatal("timeout ao ler resposta")
    assert not _gem_fatal("Connection reset by peer")
    assert _gem_estado["fatal"] is None


# ── Janela de datas (regra do dono, 28/07) ──────────────────────────────────
# "se a data for próxima, nós podemos seguir". O exame nem sempre é no dia da
# guia: DANIELLE tinha guia de 20/07 e exames em 22/07, e morria em SEM_MATCH.

def test_janela_busca_do_dia_mais_proximo_para_o_mais_distante():
    from esteira import _offsets_janela, _data_mais
    assert _offsets_janela(2) == [1, -1, 2, -2]
    assert [_data_mais("20/07/2026", o) for o in _offsets_janela(2)] == [
        "21/07/2026", "19/07/2026", "22/07/2026", "18/07/2026"]


def test_data_mais_atravessa_mes_e_rejeita_lixo():
    from esteira import _data_mais
    assert _data_mais("30/07/2026", 3) == "02/08/2026"
    assert _data_mais("01/03/2026", -1) == "28/02/2026"
    assert _data_mais("xx", 1) is None


def test_laudo_do_analitico_nunca_e_tratado_como_exame_de_fora(tmp_path):
    """LOARA (195215189, 21/07): guia pede interproximal; o laudo do accession
    40335114 — interproximal no analítico e na worklist — foi baixado como
    LAUDO_ATM_. O filtro leu 'ATM', não achou na guia e excluiu o laudo CERTO.
    O accession vindo do analítico é prova de que o exame é do convênio."""
    pasta = _pasta(tmp_path, ["LAUDO_ATM_40335114_OFICIAL.pdf", "ENTREGA_1.jpg"])
    arquivos, excluidos, _f = _filtrar_arquivos_da_gto(
        pasta, {"gto_exames_desta": ["interproximal"]},
        extras_acc=None, convenio_acc=["40335114"])
    assert sorted(os.path.basename(a) for a in arquivos) == [
        "ENTREGA_1.jpg", "LAUDO_ATM_40335114_OFICIAL.pdf"]
    assert excluidos == []


def test_sem_procedencia_a_heuristica_antiga_continua_valendo(tmp_path):
    pasta = _pasta(tmp_path, ["LAUDO_ATM_40335114_OFICIAL.pdf", "ENTREGA_1.jpg"])
    arquivos, excluidos, fora = _filtrar_arquivos_da_gto(
        pasta, {"gto_exames_desta": ["interproximal"]}, extras_acc=None, convenio_acc=None)
    assert fora == ["atm"] and excluidos
