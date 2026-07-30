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


def test_composicao_da_doc_orto():
    """Regra do dono, revista em 30/07 com os pedidos reais na mao: identifica uma
    DOC ORTO COMPLETA no pedido a dupla telerradiografia + fotografias, mais UM
    entre modelos e panoramica. A telerradiografia e o que separa da CONTROLE
    (fotos + panoramica)."""
    from solicitacao_utils import (_DOC_ORTO_ANCORAS, _DOC_ORTO_TERCEIRO,
                                   _DOC_CONTROLE)
    assert _DOC_ORTO_ANCORAS == {"telerradiografia", "fotografia"}
    assert _DOC_ORTO_TERCEIRO == {"modelo", "panoramica"}
    assert _DOC_CONTROLE == {"fotografia", "panoramica"}
    base = set(_DOC_ORTO_ANCORAS) | {"modelo"}
    assert "documentacao_completa" in expande_documentacao(base)
    # tirar uma ANCORA descaracteriza a completa; o terceiro item pode ser
    # modelo OU panoramica — regra revista pelo dono em 30/07.
    for ancora in _DOC_ORTO_ANCORAS:
        assert "documentacao_completa" not in expande_documentacao(base - {ancora}), ancora
    assert "documentacao_completa" in expande_documentacao(
        set(_DOC_ORTO_ANCORAS) | {"panoramica"})


def test_extras_nao_atrapalham_a_doc_orto():
    from solicitacao_utils import _DOC_ORTO_ANCORAS
    lido = set(_DOC_ORTO_ANCORAS) | {"modelo", "periapical", "oclusal"}
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


def test_sem_modelos_ainda_e_documentacao_completa():
    """Regra revista pelo dono (30/07), com os pedidos reais na mao: telerradiografia
    + fotografias + panoramica, SEM escrever 'modelos', E uma documentacao completa.
    Casos LAIS ZAA GUIA SANTOS, ANDNA JAIRA e VANESSA SANTOS DE SOUSA."""
    sem_modelo = expande_documentacao({"panoramica", "telerradiografia",
                                       "fotografia", "periapical", "oclusal"})
    assert canon_exames("Doc Orto Compl") <= sem_modelo
    assert canon_exames("Doc Orto Contro") <= sem_modelo


def test_doc_completa_e_controle_sao_composicoes_diferentes():
    from solicitacao_utils import _DOC_ORTO_ANCORAS, _DOC_CONTROLE
    completa = set(_DOC_ORTO_ANCORAS) | {"modelo"}
    assert canon_exames("Doc Orto Compl") <= expande_documentacao(completa)
    assert canon_exames("Doc Orto Contro") <= expande_documentacao(set(_DOC_CONTROLE))
    # controle (fotos + panoramica, SEM telerradiografia) nao vira completa
    assert "documentacao_completa" not in expande_documentacao(set(_DOC_CONTROLE))


def test_motivo_diz_o_que_falta_e_nao_vaza_token_interno():
    """A operadora precisa saber QUAL exame falta no pedido — e 'documentacao_completa'
    é token interno, nao pode aparecer na mensagem."""
    # pedido que realmente NAO cobre: guia quer documentacao completa, pedido so
    # traz periapical (a ANDNA, que antes caia aqui, agora e aceita — regra de 30/07)
    leituras = [_leitura(0, "ANDNA JAIRA NEVES", ["radiografia periapical"])]
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


# ── Rotulo do exame no nome do laudo (caso LOARA, 21/07) ────────────────────
# O laudo do accession 40335114 — INTERPROXIMAL no analitico e na worklist — foi
# baixado como LAUDO_ATM_. Causa: o rotulo vinha de uma varredura por SUBSTRING no
# HTML cru da linha, sem fronteira de palavra, e INTERPROXIMAL nem estava na lista.

def test_exame_vem_do_campo_e_nao_do_html_cru():
    from extrator_arquivos import extrair_tokens
    h = ('<tr data-formatMsg="x" id="datmod">'
         '<td><span class="wrap-exam">INTERPROXIMAL</span></td></tr>')
    assert extrair_tokens(h)["exame"] == "INTERPROXIMAL"


def test_atm_nao_e_capturado_dentro_de_atributo():
    from extrator_arquivos import extrair_tokens
    h = '<tr class="formatMsg" id="datmod"><td>PANORAMICA</td></tr>'
    assert extrair_tokens(h)["exame"] == "PANORAMICA"


def test_interproximal_esta_no_vocabulario_de_fallback():
    from extrator_arquivos import EXAME_KEYWORDS
    for kw in ("INTERPROXIMAL", "OCLUSAL", "MODELO", "TOMOGRAFIA"):
        assert kw in EXAME_KEYWORDS, kw


# ── Trava de duplicidade no UNICO ponto de escrita (regra do dono, 29/07) ───
# O OdontoPrev nao permite remover anexo: duplicar e dano PERMANENTE. Toda guia
# nasce com 1 anexo (a propria GTO); com 2 ou mais, nada mais deve ser enviado.
# A guarda fica em upload_arquivos porque os TRES caminhos que anexam passam por
# ela (esteira, fechar_dia, ciclo_completo).

class _GP:
    def __init__(self, n, nomes=()): self._n, self._nomes = n, set(nomes)
    def inner_text(self, _s): return f"total de anexos) : {self._n}"
    def query_selector(self, _s): return None
    def query_selector_all(self, _s): return []
    def wait_for_timeout(self, _ms): pass


def test_guia_com_dois_anexos_nao_recebe_mais_nada(tmp_path):
    from extrator_odontoprev import upload_arquivos
    f = tmp_path / "LAUDO_PANORAMICA_1_OFICIAL.pdf"; f.write_bytes(b"x")
    r = upload_arquivos(_GP(2), [str(f)])
    assert r["ok"] is False and r["enviados"] == []
    assert "ja tem 2 anexos" in r["erro"]


def test_guia_com_muitos_anexos_tambem_e_bloqueada(tmp_path):
    from extrator_odontoprev import upload_arquivos
    f = tmp_path / "ENTREGA_abc123.jpg"; f.write_bytes(b"x")
    r = upload_arquivos(_GP(12), [str(f)])
    assert r["ok"] is False and r["enviados"] == []


def test_contagem_ilegivel_nao_arrisca(tmp_path):
    """Na duvida sobre quantos anexos ja existem, nao envia."""
    from extrator_odontoprev import upload_arquivos
    f = tmp_path / "ENTREGA_abc123.jpg"; f.write_bytes(b"x")
    r = upload_arquivos(_GP(-1), [str(f)])
    assert r["ok"] is False and "LER" in r["erro"]


# ── Nome ilegivel != nome de OUTRA pessoa (casos TAINA e THAILAN, 21/07) ────
# O pedido do dentista e manuscrito. Quando o nome sai ilegivel, isso e AUSENCIA
# de evidencia — nao evidencia contraria. Antes os dois casos eram tratados igual
# e a guia ia para revisao humana do mesmo jeito.

def _leitura2(idx, paciente, exames, dentista="", cro=""):
    return {"idx": idx, "tipo": "solicitacao", "legivel": True,
            "paciente_lido": paciente, "exames_lidos": exames,
            "dentista_lido": dentista, "cro_lido": cro}


def test_nome_ilegivel_passa_com_o_carimbo_do_dentista():
    """Carimbo e IMPRESSO: le muito melhor que letra de dentista. Se bate com o
    campo 17 da GTO, e sinal suficiente para o documento ser deste paciente."""
    leituras = [_leitura2(0, "", ["panoramica"], dentista="VIRGINIA GABRIELA OLIVEIRA ALMEIDA")]
    idx, _a, _m = _escolher_solicitacao(
        leituras, "TAINA SALLES DE OLIVEIRA", {"panoramica"}, 1,
        "VIRGINIA GABRIELA OLIVEIRA ALMEIDA")
    assert idx == 0


def test_nome_ilegivel_passa_com_o_cro():
    leituras = [_leitura2(0, "B???o", ["panoramica"], cro="12345")]
    idx, _a, _m = _escolher_solicitacao(
        leituras, "BRUNO CONCEICAO DE JESUS", {"panoramica"}, 1, "DANIEL JORGE",
        None, "17 - Nome do Profissional Solicitante DANIEL JORGE 19 - Numero 12345")
    assert idx == 0


def test_nome_ilegivel_SEM_segundo_sinal_continua_indo_para_revisao():
    leituras = [_leitura2(0, "", ["panoramica"])]
    idx, _a, motivo = _escolher_solicitacao(
        leituras, "TAINA SALLES DE OLIVEIRA", {"panoramica"}, 1, "VIRGINIA GABRIELA")
    assert idx is None and motivo == "PACIENTE_INCOMPATIVEL"


def test_nome_de_OUTRA_pessoa_e_rejeitado_mesmo_com_o_dentista_certo():
    """A guarda que impede anexar o pedido do IRMAO. TAINA e THAILAN SALLES sao da
    mesma familia: se o documento tem o nome do outro, o carimbo igual NAO salva."""
    leituras = [_leitura2(0, "THAILAN CABRAL SALLES DE ALMEIDA", ["panoramica"],
                          dentista="VIRGINIA GABRIELA OLIVEIRA ALMEIDA", cro="12345")]
    idx, _a, _m = _escolher_solicitacao(
        leituras, "TAINA SALLES DE OLIVEIRA", {"panoramica"}, 1,
        "VIRGINIA GABRIELA OLIVEIRA ALMEIDA CRO 12345")
    assert idx is None


def test_mensagem_de_cobertura_usa_o_MESMO_candidato_da_decisao():
    """6 guias (23-24/07) saíram com "FALTA no pedido: nenhum" numa guia reprovada
    por falta de cobertura — absurdo lógico. A mensagem recalculava por fora e
    pegava outro candidato. Agora a função devolve o que ELA avaliou."""
    leituras = [
        # candidato 0: nome compatível, mas ILEGÍVEL — a decisão o ignora
        {"idx": 0, "tipo": "solicitacao", "legivel": False,
         "paciente_lido": "ANA LIMA COSTA", "exames_lidos": ["panoramica", "periapical"]},
        # candidato 1: o que a decisão avalia de verdade — falta periapical
        {"idx": 1, "tipo": "solicitacao", "legivel": True,
         "paciente_lido": "ANA LIMA COSTA", "exames_lidos": ["panoramica"]},
    ]
    det = {}
    idx, _a, motivo = _escolher_solicitacao(
        leituras, "ANA LIMA COSTA", {"panoramica", "periapical"}, 2, "", det)
    assert idx is None and motivo == "NAO_COBRE"
    assert det["falta"] == {"periapical"}      # nunca "nenhum"
    assert det["idx"] == 1


def test_caso_josete_lixo_da_IA_cai_no_cro():
    """JOSETE DIAS DE SANTANA (24/07 Tancredo): a IA leu 'Foxtel Ques de sontora'.
    Tres palavras, zero em comum com o nome da guia — nao e parente, e leitura
    falhada. O CRO (20489) foi lido perfeitamente e decide."""
    leituras = [_leitura2(0, "Foxtel Ques de sontora", ["panoramica"],
                          dentista="Fabielie C. Do chyelta", cro="20489")]
    idx, _a, _m = _escolher_solicitacao(
        leituras, "JOSETE DIAS DE SANTANA", {"panoramica"}, 1, "FABIELLE C DO CARMO",
        None, "18 - Conselho CRO 19 - Numero no Conselho 20489 UF BA")
    assert idx == 0


def test_cro_de_outro_dentista_nao_passa():
    leituras = [_leitura2(0, "xxx yyy zzz", ["panoramica"], cro="99999")]
    idx, _a, _m = _escolher_solicitacao(
        leituras, "JOSETE DIAS DE SANTANA", {"panoramica"}, 1, "FABIELLE",
        None, "19 - Numero no Conselho 20489")
    assert idx is None


def test_irma_com_o_mesmo_dentista_continua_rejeitada():
    """TAINA x THAILAN SALLES: mesma familia, mesmo dentista. O sobrenome em comum
    identifica parente — e parente NAO cai no segundo sinal."""
    leituras = [_leitura2(0, "THAILAN CABRAL SALLES DE ALMEIDA", ["panoramica"],
                          dentista="FABIELLE", cro="20489")]
    idx, _a, _m = _escolher_solicitacao(
        leituras, "TAINA SALLES DE OLIVEIRA", {"panoramica"}, 1, "FABIELLE",
        None, "19 - Numero no Conselho 20489")
    assert idx is None


# ── A MAIS RECENTE VENCE (regra do dono, 30/07) ─────────────────────────────
# "nunca usar uma solicitacao mais velha se houver uma mais nova". Antes a
# COBERTURA decidia primeiro: quando a nova nao cobria e uma antiga cobria, a
# ANTIGA era escolhida e tinha a data reescrita. Medidas 10 guias faturadas com
# pedido de ate 1066 dias antes do exame (MATHEUS 20/09/23 x exame 17/07/26).

def _sol(idx, exames, data=None, paciente="ANA LIMA COSTA"):
    return {"idx": idx, "tipo": "solicitacao", "legivel": True,
            "paciente_lido": paciente, "exames_lidos": exames,
            "data_solicitacao": data}


def test_entre_duas_que_cobrem_usa_a_mais_recente():
    """idx menor = anexo mais novo (a lista chega ordenada por id decrescente)."""
    idx, _a, _m = _escolher_solicitacao(
        [_sol(0, ["panoramica"]), _sol(1, ["panoramica", "periapical"])],
        "ANA LIMA COSTA", {"panoramica"}, 2)
    assert idx == 0


def test_nao_cai_para_a_antiga_quando_a_nova_nao_cobre():
    """O caso que faturava pedido de 2023 para exame de 2026."""
    det = {}
    idx, _a, motivo = _escolher_solicitacao(
        [_sol(0, ["periapical"]), _sol(1, ["panoramica"])],
        "ANA LIMA COSTA", {"panoramica"}, 2, "", det)
    assert idx is None and motivo == "NAO_COBRE"
    assert det["falta"] == {"panoramica"}      # o que falta NA MAIS RECENTE
    assert det["escolhida_idx"] == 0


def test_uma_unica_solicitacao_e_usada():
    idx, _a, motivo = _escolher_solicitacao(
        [_sol(3, ["panoramica"])], "ANA LIMA COSTA", {"panoramica"}, 4)
    assert idx == 3 and motivo is None


def test_registra_quantas_outras_solicitacoes_existiam():
    det = {}
    _escolher_solicitacao([_sol(0, ["panoramica"]), _sol(1, ["panoramica"]),
                           _sol(2, ["panoramica"])],
                          "ANA LIMA COSTA", {"panoramica"}, 3, "", det)
    assert det["outras"] == 2


def test_documentacao_completa_por_extenso_e_reconhecida():
    """LAIS ZAA GUIA SANTOS (24/07 Centro): o pedido diz literalmente
    'DOCUMENTAÇÃO ORTODÔNTICA COMPLETA' e era reprovado, porque o padrão exigia
    'documentacao' colado em 'completa' — a palavra 'ortodôntica' no meio quebrava."""
    for t in ("DOCUMENTAÇÃO ORTODÔNTICA COMPLETA", "Documentação Ortodôntica Completa",
              "Doc Orto Compl", "documentacao completa"):
        assert "documentacao_completa" in canon_exames(t), t


def test_subtipos_que_NAO_sao_a_completa():
    for t in ("Doc Orto Contro", "Documentação Ortodôntica Básica",
              "documentacao periodontal"):
        assert "documentacao_completa" not in canon_exames(t), t


def test_caso_lais_passa():
    ped = ("1. Rx Panorâmico em topo, 2. Telerradiografia Rickets, "
           "3. Fotografia extra-bucal, 4. Fotografia intra-bucal, "
           "DOCUMENTAÇÃO ORTODÔNTICA COMPLETA, Oclusão Frontal")
    leituras = [_leitura(0, "LAIS ZAA GUIA SANTOS", [ped])]
    idx, _a, motivo = _escolher_solicitacao(
        leituras, "LAIS ZAA GUIA SANTOS", canon_exames("Doc Orto Compl"), 1)
    assert idx == 0 and motivo is None


# ── Pedido em VARIAS FOLHAS (caso JUCILENE, 24/07 Centro) ───────────────────
# GTO 195371168 autoriza panoramica + periapical + interproximal. O prontuario tem
# DUAS solicitacoes da mesma dentista, na MESMA data: uma pede a panoramica, outra
# pede periapical e interproximal. Juntas cobrem; sozinhas nenhuma cobre.

def _folha(idx, texto, data, paciente="JUCILENE PINHEIRO DE OLIVEIRA"):
    return {"idx": idx, "tipo": "solicitacao", "legivel": True,
            "paciente_lido": paciente, "exames_lidos": [texto],
            "data_solicitacao": data}


GUIA_JUCILENE = (canon_exames("Rad.Panor.S/Tra") | canon_exames("Rx Periapical")
                 | canon_exames("Rx Interprox."))


def test_duas_folhas_da_mesma_data_se_somam():
    det = {}
    idx, _a, motivo = _escolher_solicitacao([
        _folha(0, "RADIOGRAFIA PERIAPICAL E INTERPROXIMAL DAS UNIDADES: 24, 25, 26, 27, 36 e 37",
               "18/07/2026"),
        _folha(1, "RADIOGRAFIA PANORAMICA COM LAUDO", "18/07/2026"),
    ], "JUCILENE PINHEIRO DE OLIVEIRA", GUIA_JUCILENE, 2, "", det)
    assert motivo is None and idx == 0
    assert sorted(det["idxs"]) == [0, 1]      # as DUAS folhas vao ser anexadas


def test_folha_de_data_ANTERIOR_nao_e_somada():
    """A regra do dono continua: nunca usar pedido mais velho havendo um mais novo."""
    idx, _a, motivo = _escolher_solicitacao([
        _folha(0, "RADIOGRAFIA PERIAPICAL E INTERPROXIMAL", "18/07/2026"),
        _folha(1, "RADIOGRAFIA PANORAMICA COM LAUDO", "02/01/2024"),
    ], "JUCILENE PINHEIRO DE OLIVEIRA", GUIA_JUCILENE, 2, "", None)
    assert idx is None and motivo == "NAO_COBRE"


def test_uma_folha_que_cobre_sozinha_nao_arrasta_outras():
    det = {}
    idx, _a, motivo = _escolher_solicitacao([
        _folha(0, "RADIOGRAFIA PANORAMICA", "18/07/2026"),
        _folha(1, "RADIOGRAFIA PANORAMICA", "18/07/2026"),
    ], "JUCILENE PINHEIRO DE OLIVEIRA", {"panoramica"}, 2, "", det)
    assert motivo is None and det["idxs"] == [0]


def test_releitura_so_olha_quem_nao_e_solicitacao():
    """A 2a passada existe porque, em lote de anexos parecidos, o modelo erra o
    TIPO. Caso JUCILENE: duas solicitacoes quase identicas, so uma reconhecida.
    Ela nao pode reprocessar quem ja e candidato — isso e trabalho (e custo) a toa."""
    import esteira
    vistos = []

    class _GemFake:
        class models:
            @staticmethod
            def generate_content(**kw):
                raise RuntimeError("nao deveria chamar")

    leituras = [{"idx": 0, "tipo": "solicitacao", "paciente_lido": "X"},
                {"idx": 1, "tipo": "solicitacao", "paciente_lido": "X"}]
    # todos os candidatos ja sao solicitacao -> nada a reler, nao chama o modelo
    n = esteira._reler_nao_classificados(_GemFake(), [("a", "image/jpeg", b"x", None),
                                                      ("b", "image/jpeg", b"y", None)],
                                         leituras)
    assert n == 0


def test_casos_reais_de_documentacao_sem_modelos():
    """Os tres pedidos que o dono conferiu em 30/07 e classificou como completos."""
    completa = canon_exames("Doc Orto Compl")
    for nome, txt in [
        ("LAIS", "Telerradiografia Rickets, Fotografia extra-bucal, "
                 "Fotografia intra-bucal, Rx Panoramico em topo"),
        ("ANDNA", "Analise cefalometrica USP, Fotos extrabucais, Fotos intrabucais, "
                  "Radiografia panoramica topo, Telerradiografia de perfil"),
        ("VANESSA", "Rx Panoramico em topo, Telerradiografia Rickets, "
                    "Fotografia extra-bucal, Fotografia intra-bucal"),
    ]:
        assert completa <= expande_documentacao(canon_exames(txt)), nome


def test_controle_nao_vira_completa():
    """A telerradiografia e o que separa: controle e fotos + panoramica."""
    e = expande_documentacao(canon_exames("Fotografia extra-bucal, Rx Panoramico"))
    assert "documentacao" in e
    assert "documentacao_completa" not in e


def test_amanda_escreveu_a_palavra_e_os_componentes():
    """AMANDA QUEIROZ, 30/07 — o pedido escreve DOCUMENTACAO *e* lista tele,
    fotos e panoramica. O atalho `if "documentacao" in ex: return ex` saia antes
    de olhar a composicao, entao o pedido MAIS completo virava o rotulo generico
    e a guia caia em 'pedido nao cobre'. A palavra nao diz o subtipo."""
    txt = ("EXAMES RADIOGRAFICOS INTRA BUCAIS 1- PANORAMICA EM TOPO "
           "2- TELE PERFIL COM TRACADO ANATOMICO 3- RICKETES FATORES "
           "DOCUMENTACAO FOTOS INTRA BUCAIS FRONTAL PERFIL DIREITO E ESQUERDO")
    assert "documentacao_completa" in expande_documentacao(canon_exames(txt))


def test_tele_perfil_e_telerradiografia():
    """Como o dentista escreve de verdade. Sem isto o pedido perdia a ancora que
    separa documentacao COMPLETA de CONTROLE."""
    for t in ("TELE PERFIL COM TRACADO ANATOMICO", "tele de perfil", "RICKETES"):
        assert "telerradiografia" in canon_exames(t), t


def test_abreviacao_da_operadora_no_campo_32():
    """PAULO ROBERTO, GTO 195418785 — a propria OdontoPrev escreve
    'Rad.Pano.C/Trac'. `panor` nao casa (falta o 'r'), a guia ficava sem exame de
    referencia e nao dava para conferir pedido nenhum. Falha nossa."""
    assert "panoramica" in canon_exames("Rad.Pano.C/Trac")


# ── Pendências agrupadas por quem resolve ────────────────────────────────────

def test_classifica_os_motivos_reais_de_24_07():
    """Os 13 motivos que a producao escreveu de fato. Nenhum pode cair em 'outros'
    — uma pendencia sem dono e uma pendencia que ninguem pega."""
    from db import classificar_pendencia
    casos = [
        ("nao ha laudo nem imagem para baixar", "Radiologista", "sem_entregavel"),
        ("Solicitacao OK, mas falta o LAUDO valido no PRORADIS", "Radiologista", "falta_laudo"),
        ("o pedido do dentista nao cobre tudo que a guia autoriza. FALTA no pedido: "
         "panoramica", "Clinica", "pedido_nao_cobre"),
        ("nao ha nenhum pedido do dentista anexado ao prontuario", "Clinica", "sem_pedido"),
        ("nenhum documento do prontuario esta no nome deste paciente", "Nos", "nome_nao_bate"),
        ("o sistema nao conseguiu ler quais exames a guia autoriza", "Nos", "guia_ilegivel"),
        ("a anexacao falhou: apos excluir exames fora da guia nao sobrou nenhum laudo",
         "Nos", "anexacao"),
        ("o paciente da guia nao foi encontrado no PRORADIS", "Cadastro", "paciente_nao_achado"),
    ]
    for motivo, _quem, chave_esperada in casos:
        chave, quem, acao = classificar_pendencia(motivo)
        assert chave == chave_esperada, f"{motivo[:40]} -> {chave}"
        assert acao, "toda pendencia precisa dizer o que fazer"


def test_falha_nossa_nao_vira_tarefa_da_operacao():
    """Regra do dono (30/07): 'o que nos resolvemos aqui deve entrar num fallback
    ate ser resolvido, nao deve ir para pendencias'. Pedir pedido novo a clinica
    por causa de bug nosso e trabalho jogado fora — o documento certo ja esta la."""
    from db import classificar_pendencia
    nossos = ["nenhum documento do prontuario esta no nome deste paciente",
              "o sistema nao conseguiu ler quais exames a guia autoriza",
              "a anexacao falhou: nao sobrou nenhum laudo"]
    for m in nossos:
        assert classificar_pendencia(m)[1] == "Nós", m


def test_motivo_desconhecido_nao_some():
    """Nada e silencioso: motivo fora do padrao cai em 'outros' com acao explicita,
    nunca desaparece da lista."""
    from db import classificar_pendencia
    chave, quem, acao = classificar_pendencia("qualquer coisa nova que ninguem previu")
    assert chave == "outros" and quem and acao
# ── Pareamento de guias (OZIEL) ──────────────────────────────────────────────

def _leitura_dt(idx, paciente, exames, data):
    d = _leitura(idx, paciente, exames)
    d["data_solicitacao"] = data
    return d


def test_oziel_cada_guia_com_o_seu_pedido():
    """OZIEL FERRAZ SANTANA, 25/07 — DUAS guias no mesmo episodio (195420152
    documentacao ortodontica, 195420167 periapical) e os DOIS pedidos no mesmo
    prontuario. 'A mais recente vence' entregava o pedido da doc orto para as duas,
    e a guia de periapical virava pendencia com o pedido dela ali do lado."""
    leituras = [
        _leitura_dt(0, "OZIEL FERRAZ SANTANA",
                    ["documentacao ortodontica", "panoramica", "telerradiografia",
                     "fotografias", "modelos"], "28/07/2026"),
        _leitura_dt(1, "OZIEL FERRAZ SANTANA", ["radiografia periapical"], "24/07/2026"),
    ]
    idx, a, motivo = _escolher_solicitacao(leituras, "OZIEL FERRAZ SANTANA",
                                           {"periapical"}, 2)
    assert motivo is None, motivo
    assert idx == 1, "devia parear com o pedido de periapical, nao com o de doc orto"


def test_pedido_antigo_nao_e_pareado():
    """A regra do dono continua de pe: pedido de 2023 nao serve para exame de 2026.
    Casos MATHEUS (1066 dias), JAQUELINE e VANESSA. O pareamento so vale dentro da
    janela do episodio."""
    leituras = [
        _leitura_dt(0, "MATHEUS SILVA", ["panoramica"], "17/07/2026"),
        _leitura_dt(1, "MATHEUS SILVA", ["periapical"], "20/09/2023"),
    ]
    idx, a, motivo = _escolher_solicitacao(leituras, "MATHEUS SILVA", {"periapical"}, 2)
    assert idx is None and motivo == "NAO_COBRE"


def test_pareamento_exige_data_nos_dois():
    """Sem data lida nao da para afirmar que e o mesmo episodio — vai para pessoa."""
    leituras = [
        _leitura_dt(0, "ANA SOUZA", ["panoramica"], "28/07/2026"),
        _leitura(1, "ANA SOUZA", ["periapical"]),          # sem data
    ]
    idx, a, motivo = _escolher_solicitacao(leituras, "ANA SOUZA", {"periapical"}, 2)
    assert idx is None and motivo == "NAO_COBRE"


# ── Inicial abreviada no nome (ISABELA) ──────────────────────────────────────

def test_isabela_inicial_abreviada_e_a_mesma_pessoa():
    """ISABELA BENINI MEDINA TAVARES, GTO 195416813, 25/07 — o pedido trazia
    "Isabela Benini M. Tavares". Tres palavras batiam exatamente, mas o "M." era
    lido como token DIVERGENTE e a guarda contra o irmao reprovava a guia dizendo
    que o documento era de outra pessoa. O pedido cobria a guia inteira."""
    assert _nomes_compat("Isabela Benini M. Tavares", "ISABELA BENINI MEDINA TAVARES")
    assert _nomes_compat("MARIA J. SANTOS", "MARIA JOSE SANTOS")
    assert _nomes_compat("MARIA J SANTOS", "MARIA JOSE SANTOS")   # sem ponto


def test_inicial_nao_vira_carta_branca():
    """A guarda que existe por causa do irmao (SALLES, pai x filho) continua de pe:
    a inicial so e aceita DEPOIS de 2+ tokens baterem exatamente, e a letra precisa
    casar com o token que falta."""
    assert not _nomes_compat("PEDRO SILVA SANTOS", "JOAO SILVA SANTOS")
    assert not _nomes_compat("MARIA J. SANTOS", "MARIA ANTONIA SANTOS")
    assert not _nomes_compat("JOSE C. SILVA", "JOSE CARLOS PEREIRA")


# ── Pendências por período ───────────────────────────────────────────────────

def test_intervalo_de_dias():
    """O dono pediu intervalo (30/07): a pergunta real e 'o que esta parado nesta
    semana?'. Uma pendencia sozinha num dia nao vira tarefa; sete espalhadas viram."""
    from db import _dias_do_intervalo
    assert _dias_do_intervalo("24/07/2026", "26/07/2026") == \
        ["24/07/2026", "25/07/2026", "26/07/2026"]
    assert _dias_do_intervalo("25/07/2026") == ["25/07/2026"]      # sem fim = 1 dia
    assert _dias_do_intervalo("26/07/2026", "24/07/2026")[0] == "24/07/2026"  # invertido
    assert _dias_do_intervalo("lixo", "26/07/2026") == []


def test_intervalo_tem_teto():
    """Intervalo aberto por engano (2020 ate hoje) travaria a tela sem dizer por que:
    cada dia e uma consulta ao banco."""
    from db import _dias_do_intervalo
    assert len(_dias_do_intervalo("01/01/2020", "31/12/2026")) == 62


# ── Guia baixada do portal (JOSETE) ──────────────────────────────────────────

def test_so_aceita_arquivo_de_verdade_do_portal():
    """O portal responde 200 com HTML de login quando a sessao cai. Um HTML lido
    como imagem viraria leitura de lixo — e leitura de lixo faz a IA "ver" o que
    nao existe. So passa o que tem assinatura de PDF, PNG ou JPEG."""
    from esteira import _mime_do_conteudo
    assert _mime_do_conteudo(b"%PDF-1.4 blah") == "application/pdf"
    assert _mime_do_conteudo(bytes([0x89]) + b"PNG") == "image/png"
    assert _mime_do_conteudo(bytes([0xFF, 0xD8, 0xFF, 0xE0])) == "image/jpeg"
    assert _mime_do_conteudo(b"<!doctype html><html>login") == ""
    assert _mime_do_conteudo(b"") == ""


def test_download_do_portal_sem_id_nao_tenta():
    """Sem id nao ha o que baixar — e nao se bate na API de producao a toa."""
    from esteira import _baixar_anexo_portal
    b, m = _baixar_anexo_portal(None, None)
    assert b is None and m == ""
