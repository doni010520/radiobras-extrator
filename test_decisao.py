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


def _solk(idx, ex, cro="2556", data=None, pac="KAEL LIMA FONSECA MACEDO"):
    return {"idx": idx, "tipo": "solicitacao", "legivel": True, "paciente_lido": pac,
            "cro_lido": cro, "data_solicitacao": data, "exames_lidos": ex}


# LIMITAÇÃO CONHECIDA — folhas SEM DATA não são unidas automaticamente.
# Tentamos (29/07, caso KAEL: 2 folhas do mesmo pedido sem data) um fallback que
# tratava "sem data" como hoje e unia por CRO. Review adversarial provou que o
# gatilho do acerto (2 folhas sem data, mesma dentista, cobrem juntas) é IDÊNTICO
# ao gatilho do erro (2 PEDIDOS de episódios diferentes, sem data, mesma dentista)
# — sem um sinal de recência confiável não dá pra separar, e anexar o pedido errado
# é irreversível (reabre o buraco anti-2023 que o dono mandou fechar). Além disso o
# CRO "só dígitos" colide entre dentistas de UFs diferentes. Então: sem data ->
# PENDÊNCIA (conferência manual). Melhor pendência que anexação errada.

def test_folhas_sem_data_nao_unem_ficam_pendencia():
    # Duas folhas sem data (mesma dentista) NÃO unem sozinhas — vira pendência.
    leituras = [_solk(0, ["panoramica"]), _solk(1, ["periapical"])]
    idx, _a, motivo = _escolher_solicitacao(leituras, "KAEL LIMA FONSECA MACEDO",
                                            {"periapical"}, 2)
    assert idx is None


def test_pedido_velho_datado_nao_e_puxado_pela_recente_sem_data():
    # REGRA DO DONO (anti-2023): a periapical que cobre é de 20/09/2023 (data lida);
    # a mais recente (panorâmica, sem data) não cobre. A recente sem data não dispara
    # união/pareamento — continua pendência, sem puxar o pedido de 2023.
    leituras = [_solk(0, ["panoramica"], data=None),
                _solk(1, ["periapical"], data="20/09/2023")]
    idx, _a, motivo = _escolher_solicitacao(leituras, "KAEL LIMA FONSECA MACEDO",
                                            {"periapical"}, 2)
    assert idx is None


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


def test_ocr_grudou_dois_tokens_casa():
    # IAN SACRAMENTO RODRIGUES (30/07 Tancredo): o OCR leu 'IANSACRAMENTO RODRIGUES'
    # (perdeu o espaço). É a MESMA pessoa — o token grudado são dois tokens
    # ADJACENTES do outro lado. Antes morria no "< 2 tokens em comum".
    assert _nomes_compat("IANSACRAMENTO RODRIGUES", "IAN SACRAMENTO RODRIGUES")


def test_ocr_grudado_nao_abre_porta_pra_irmao():
    # A concatenação só casa a MESMA sequência de letras: 'PEDROSILVA' nunca vira
    # 'JOAO SILVA' — o irmão continua barrado.
    assert not _nomes_compat("PEDROSILVA SANTOS", "JOAO SILVA SANTOS")
    # E um token grudado que não corresponde a tokens adjacentes do alvo não casa.
    assert not _nomes_compat("MARIACLARA SOUZA", "ANA PAULA SOUZA")


def test_ocr_grudado_tokens_repetidos_nao_quebra():
    # Nome com dois tokens IDÊNTICOS adjacentes ('MARIA MARIA SILVA') grudado em
    # 'MARIAMARIA SILVA': o par concatenado vinha (a, a) e o remove duplicado
    # estourava ValueError. Não pode quebrar — e ainda casa (mesma pessoa).
    assert _nomes_compat("MARIAMARIA SILVA", "MARIA MARIA SILVA")
    assert _nomes_compat("ANAANA SOUZA", "ANA ANA SOUZA")


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


# ── Cron diário D-4 + re-tentativa de pendências (Feature 2) ──────────────────
# O cron das 5h fatura D-4 nas 3 unidades E re-roda os dias com pendência aberta
# no prazo; quando uma guia fatura no reprocesso, a pendência dela fecha sozinha
# dentro do salvar_execucao. Dois defeitos travavam isso: era D-3, e a gravação
# usava uma variável `_j` inexistente (NameError engolido -> nada era salvo ->
# pendência nunca fechava -> o alerta de SLA seguia cobrando guia já faturada).

def test_dia_alvo_cron_e_D_menos_4():
    """O alvo do cron é D-4 (quatro dias atrás), não D-3."""
    from datetime import date
    import app
    assert app._dia_alvo_cron(date(2026, 8, 2)) == "29/07/2026"   # atravessa o mês
    assert app._dia_alvo_cron(date(2026, 8, 10)) == "06/08/2026"


def _mock_cron_deps(monkeypatch, resumo=None, erro=None):
    """Isola _faturar_cron_body: 1 unidade, sem pendências, esteira mockada."""
    import app, esteira
    app._esteira_ativas.clear()
    monkeypatch.setattr(app, "PLANOS", {"388336": {}})
    monkeypatch.setattr(app.db, "dias_com_pendencia_aberta", lambda prazo=None: [])
    monkeypatch.setattr(app, "_esteira_reservar", lambda dia, conta, tag: tag)
    monkeypatch.setattr(app, "_esteira_liberar", lambda dia, conta, tag: None)
    monkeypatch.setattr(app.db, "get_portal_senha", lambda c: "x")
    monkeypatch.setattr(app.db, "cron_marcar_faturar", lambda d: None)
    monkeypatch.setattr(app, "_enviar_alertas_sla", lambda: None)
    def _fake(*a, **k):
        if erro:
            raise erro
        return resumo
    monkeypatch.setattr(esteira, "rodar_esteira", _fake)


def test_cron_grava_execucao_sem_bug_do_j(monkeypatch):
    """Regressão do `_j`: o cron TEM de gravar a execução. Antes o NameError era
    engolido, salvar_execucao nunca rodava e a pendência nunca fechava."""
    import app
    resumo = {"anexado_ok": 2, "pendentes": 5, "data": "29/07/2026", "conta": "388336"}
    _mock_cron_deps(monkeypatch, resumo=resumo)
    salvos = []
    monkeypatch.setattr(app.db, "salvar_execucao",
                        lambda r, log=None: salvos.append((r, log)) or 1)
    app._faturar_cron_body()
    assert len(salvos) == 1
    assert salvos[0][0] is resumo
    # o log capturado é uma LISTA (as funções do db fazem o join), nunca uma string
    assert isinstance(salvos[0][1], list)


def test_cron_grava_falha_sem_bug_do_j(monkeypatch):
    """O 2º `_j` (no except) também não pode estourar: falha vira registro de falha."""
    import app
    _mock_cron_deps(monkeypatch, erro=RuntimeError("proxy caiu"))
    falhas = []
    monkeypatch.setattr(app.db, "salvar_execucao_falha",
                        lambda dia, conta, flag, msg, log=None: falhas.append((dia, conta, msg)) or 1)
    monkeypatch.setattr(app.db, "salvar_execucao",
                        lambda r, log=None: pytest.fail("não deveria salvar sucesso no caminho de erro"))
    app._faturar_cron_body()
    assert len(falhas) == 1
    assert "proxy" in falhas[0][2]


def test_cron_erro_ao_salvar_nao_vira_falha(monkeypatch):
    """Se rodar_esteira faturou mas salvar_execucao falha (hiccup do banco), o dia
    NÃO pode ser marcado como FALHOU — o faturamento já aconteceu (a guia já está
    anexada). Espelha o handler web (app.py), que engole o erro de gravação."""
    import app
    resumo = {"anexado_ok": 3, "data": "29/07/2026", "conta": "388336"}
    _mock_cron_deps(monkeypatch, resumo=resumo)
    def _boom(r, log=None):
        raise RuntimeError("db timeout")
    monkeypatch.setattr(app.db, "salvar_execucao", _boom)
    falhas = []
    monkeypatch.setattr(app.db, "salvar_execucao_falha",
                        lambda dia, conta, flag, msg, log=None: falhas.append(msg) or 1)
    app._faturar_cron_body()          # não pode estourar
    assert falhas == []               # erro de gravação != falha da execução


# ── Bug da barra no nome do exame (laudo pronto perdido no save) ─────────────
# 'PANORAMICA C/ TRACADO' tem '/', separador de caminho. Sem sanitizar, o
# open(os.path.join(out_dir, "LAUDO_PANORAMICA C/ TRACADO_<acc>_OFICIAL.pdf"))
# tenta gravar numa subpasta 'LAUDO_PANORAMICA C' inexistente -> FileNotFoundError
# engolido -> o laudo (pronto!) some, a guia morre 'sem laudo'. Caso real:
# PAULO SERGIO SILVA DA ROCHA, GTO 195454210, 27/07 (Pendencia id=467).

def test_laudo_com_barra_no_nome_grava_em_pasta_plana(tmp_path):
    """O nome do arquivo de laudo NÃO pode conter separador de caminho, senão o
    open() joga o laudo numa subpasta inexistente e ele é perdido."""
    import extrator_arquivos as ea
    fname = ea._laudo_fname("PANORAMICA C/ TRACADO", "40337051", "OFICIAL")
    assert "/" not in fname and "\\" not in fname
    # grava de fato numa pasta plana (reproduz o cenário real do container)
    p = tmp_path / fname
    p.write_bytes(b"%PDF-1.4 conteudo do laudo")
    assert p.exists() and p.read_bytes().startswith(b"%PDF")


def test_laudo_fname_preserva_reconhecimento_do_exame():
    """Sanitizar a '/' não pode cegar o casamento: _exame_do_laudo + canon ainda
    têm de reconhecer 'panoramica' no nome saneado."""
    import extrator_arquivos as ea
    from esteira import _exame_do_laudo
    fname = ea._laudo_fname("PANORAMICA C/ TRACADO", "40337051", "OFICIAL")
    assert "panoramica" in _exame_do_laudo(fname)


def test_laudo_fname_sem_barra_fica_igual():
    """Exame sem char ilegal não deve ser alterado (tomografia continua legível)."""
    import extrator_arquivos as ea
    fname = ea._laudo_fname("TOMOGRAFIA COMPUTADORIZADA CONE BEAM- POR REGIAO", "40337054", "OFICIAL")
    assert fname == "LAUDO_TOMOGRAFIA COMPUTADORIZADA CONE BEAM- POR REGIAO_40337054_OFICIAL.pdf"


# ── Aviso "exame pronto SEM GUIA" — detecção por procedência ─────────────────
# Sem guia = laudo pronto excluído por PROCEDÊNCIA (accession em extras_acc = não
# veio do convênio → nenhuma guia cobre). Distinto do excluído por tipo de exame,
# que pode ser de OUTRA guia do paciente. Caso PAULO: tomografia é sem guia.

def test_laudos_sem_guia_pega_so_procedencia():
    from esteira import _laudos_sem_guia
    excluidos = [
        "LAUDO_TOMOGRAFIA COMPUTADORIZADA CONE BEAM- POR REGIAO_40337054_OFICIAL.pdf",  # extra
        "LAUDO_PERIAPICAL_40295386_OFICIAL.pdf",   # do convênio -> outra guia, NÃO é sem guia
        "ENTREGA_abc1234567.jpg",                  # não é laudo -> ignora
    ]
    r = _laudos_sem_guia(excluidos, extras_acc={"40337054"})
    assert {x["accession"] for x in r} == {"40337054"}
    assert "tomografia" in r[0]["exame"].lower()
    assert r[0]["arquivo"].startswith("LAUDO_")


def test_laudos_sem_guia_vazio_quando_nada_e_extra():
    from esteira import _laudos_sem_guia
    assert _laudos_sem_guia(["LAUDO_PERIAPICAL_40295386_OFICIAL.pdf"], extras_acc=set()) == []
    assert _laudos_sem_guia([], extras_acc={"40337054"}) == []
    assert _laudos_sem_guia(None, None) == []


# ── Ajuste de data em PDF (renderiza a página p/ imagem) ─────────────────────
# Caso SIDNEY (27/07): solicitação em PDF com data lida como vencida. O ajuste de
# data edita IMAGEM (PIL); PDF caía em revisão. Fix: renderizar a página do PDF
# para imagem (PyMuPDF/fitz) e aplicar o mesmo ajuste.

def test_pdf_para_imagem_converte_pagina():
    import io
    import fitz  # PyMuPDF (já é dependência)
    from PIL import Image
    from esteira import _pdf_para_imagem
    doc = fitz.open()
    pg = doc.new_page(width=320, height=440)
    pg.insert_text((40, 60), "Solicitacao de Radiografias")
    pdf_bytes = doc.tobytes()
    doc.close()
    img_bytes, mime = _pdf_para_imagem(pdf_bytes)
    assert "image" in mime
    im = Image.open(io.BytesIO(img_bytes))
    assert im.width > 0 and im.height > 0     # abre como imagem editável pelo PIL


def test_pdf_para_imagem_lixo_retorna_none():
    from esteira import _pdf_para_imagem
    assert _pdf_para_imagem(b"nao e um pdf") is None
    assert _pdf_para_imagem(b"") is None


def test_pdf_multipagina_nao_converte():
    """Achado do code review: multi-página NÃO pode virar 1 imagem (truncaria as
    páginas 2+ num upload irreversível) -> None -> cai no fluxo antigo (revisão p/
    vencida, PDF original p/ inserir)."""
    import fitz
    from esteira import _pdf_para_imagem
    doc = fitz.open()
    doc.new_page(width=300, height=400); doc.new_page(width=300, height=400)
    pdf2 = doc.tobytes(); doc.close()
    assert _pdf_para_imagem(pdf2) is None


# ── Pedido ILEGÍVEL != pedido que não cobre (caso MARIA CLARA) ───────────────
# O Gemini não decifrou a caligrafia do pedido ('periapical' -> 'perigeed'). Antes
# a mensagem culpava a clínica ("pedir um pedido que inclua periapical") por um
# pedido que EXISTE e pode até cobrir. Preferência do dono (02/08): dizer que a
# GRAFIA estava ilegível, e não jogar a culpa na clínica.

def test_motivo_nao_cobre_distingue_ilegivel_de_nao_cobre():
    from esteira import _motivo_nao_cobre
    # pedido ILEGÍVEL (cn vazio: não leu os exames) -> caligrafia ilegível
    m_ile = _motivo_nao_cobre(["periapical"], ["periapical"], [])
    assert "ilegível" in m_ile.lower()
    assert "à mão" in m_ile.lower()
    assert "pedir à clínica" not in m_ile.lower()
    # pedido LEGÍVEL que não cobre -> falta X, pedir à clínica (como antes)
    m_cob = _motivo_nao_cobre(["periapical"], ["periapical"], ["panoramica"])
    assert "pedir à clínica" in m_cob.lower()
    assert "FALTA no pedido" in m_cob


def test_pedido_ilegivel_nao_e_classificado_como_clinica():
    from esteira import _motivo_nao_cobre
    from db import classificar_pendencia
    m = _motivo_nao_cobre(["periapical"], ["periapical"], [])     # ilegível
    chave, resp, _ = classificar_pendencia(m)
    assert chave == "pedido_ilegivel" and resp != "Clínica"
    # o legível-que-não-cobre continua na Clínica
    m2 = _motivo_nao_cobre(["periapical"], ["periapical"], ["panoramica"])
    _, resp2, _ = classificar_pendencia(m2)
    assert resp2 == "Clínica"


# ── Recuperação de exame mal lido por prefixo (caso MARIA CLARA) ─────────────
# O Gemini garbleou 'periapical' -> 'perigeed'. Recuperação SÓ na solicitação
# (recuperar=True): palavra >=6 letras, não reconhecida, cujo início (4 letras)
# bate EXATAMENTE um exame. NUNCA no laudo/GTO (recuperar=False) — lá matching
# errado anexaria laudo errado.

def test_canon_recupera_exame_mal_lido_com_recuperar():
    from solicitacao_utils import canon_exames
    assert "periapical" not in canon_exames("Rx perigeed de unibale 21")            # sem recuperar
    assert "periapical" in canon_exames("Rx perigeed de unibale 21", recuperar=True)  # com recuperar


def test_canon_sem_recuperar_e_o_default_seguro_do_laudo_gto():
    from solicitacao_utils import canon_exames
    # default (laudo/GTO) NÃO recupera -> o matching seguro fica intacto
    assert canon_exames("perigeed") == set()
    # exame legível é idêntico com/sem recuperar (não muda nada do que já funciona)
    assert canon_exames("periapical") == canon_exames("periapical", recuperar=True) == {"periapical"}


def test_canon_recuperar_nao_inventa_de_palavra_desconhecida_ou_curta():
    from solicitacao_utils import canon_exames
    # 'unibale' (garble de 'unidade') não é exame -> não recupera nada
    assert canon_exames("unibale coisa xyzabc", recuperar=True) == set()
    # prefixo ambíguo/curto não inventa
    assert "periapical" not in canon_exames("peri", recuperar=True)       # <6 letras


def test_canon_recuperar_barra_furos_do_review():
    """Achados do code review adversarial: palavra REAL do português ou de outro
    exame que colide pelo prefixo NÃO pode recuperar (cobriria falso, irreversível)."""
    from solicitacao_utils import canon_exames, expande_documentacao
    # periodontal/periodontia (palavra real) -> NÃO vira periapical
    assert canon_exames("TRATAMENTO PERIODONTAL", recuperar=True) == set()
    assert canon_exames("avaliacao periodontia", recuperar=True) == set()
    # inteiro/interno -> NÃO vira interproximal
    assert "interproximal" not in canon_exames("Rx da arcada inteira", recuperar=True)
    assert "interproximal" not in canon_exames("Rx da face interna", recuperar=True)
    # telefone/televisao -> NÃO vira telerradiografia
    assert "telerradiografia" not in canon_exames("contato telefone", recuperar=True)
    assert "telerradiografia" not in canon_exames("televisao aqui", recuperar=True)
    # fotopolimerizacao -> NÃO vira fotografia
    assert "fotografia" not in canon_exames("fotopolimerizacao da resina", recuperar=True)
    # escalada de doc: 'telefone' não completa o pacote doc-orto
    r = expande_documentacao(canon_exames("FOTOGRAFIAS MODELOS TELEFONE", recuperar=True))
    assert "documentacao_completa" not in r and "telerradiografia" not in r
    # o garble INTENCIONAL continua recuperando
    assert canon_exames("Rx perigeed de 21", recuperar=True) == {"periapical"}


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
    # Caixa alucinada (invertida ou fora de 0-1000) e REJEITADA — a anexacao e
    # irreversivel e carimbar data em lugar errado seria dano (code review 31/07)
    assert _box4([30, 20, 10, 40]) is None      # ymax < ymin
    assert _box4([10, 40, 30, 20]) is None      # xmax < xmin
    assert _box4([10, 20, 30, 1400]) is None    # fora da escala 0-1000
    assert _box4([-5, 20, 30, 40]) is None       # negativo


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


# ── Pedido em VÁRIAS SEÇÕES: a seção das FOTOS não pode ser perdida na LEITURA
#    (caso AMANDA QUEIROZ / MAYSA 195516236 e MIRIAN 195515738, 28/07) ──────────
# O pedido da Dra. Amanda Queiroz tem DUAS seções: (1) "EXAMES RADIOGRÁFICOS":
# panorâmica em topo, tele perfil com traçado, Ricketts; e (2) "DOCUMENTAÇÃO /
# FOTOS INTRA BUCAIS": frontal, perfil D/E, sorriso, arcos. A transcrição do Gemini
# só capturava a seção 1 e PERDIA a 2 — sem 'fotografia', expande_documentacao não
# promovia a documentação_completa e a guia (que autoriza doc orto completa) era
# REPROVADA. O fix é de LEITURA (o prompt passa a transcrever TODAS as seções); a
# REGRA de decisão não muda. Estes testes travam a LÓGICA que a leitura alimenta.
#
# NOTA: o prompt do Gemini em si NÃO é testável por unidade (depende de rede e do
# modelo). A correção do prompt (_DECISAO_PROMPT / _RELEITURA_PROMPT /
# _RELEITURA_TIPO_PROMPT em esteira.py) precisa de validação em RUN REAL sobre as
# guias 195516236 e 195515738. O que se pode travar aqui é: DADO o texto inteiro
# do pedido, a decisão vira documentação_completa; e dado só a seção 1, NÃO vira
# (prova de que o fix não afrouxa a regra).

AMANDA_SECAO1 = ("EXAMES RADIOGRAFICOS INTRA BUCAIS 1-PANORAMICA EM TOPO "
                 "2-TELE PERFIL COM TRACADO ANATOMICO 3-RICKETES FATORE")
AMANDA_SECAO2 = ("DOCUMENTACAO FOTOS INTRA BUCAIS FRONTAL PERFIL DIREITO E ESQUERDO "
                 "SORRISO FRONTAL PERFIL DIREITO E ESQUERDO E ARCOS")
AMANDA_PEDIDO_INTEIRO = AMANDA_SECAO1 + " " + AMANDA_SECAO2


def test_amanda_queiroz_pedido_inteiro_vira_documentacao_completa():
    """Lendo as DUAS seções, o canon vê panorâmica + telerradiografia + fotografia
    + documentação e expande promove a documentação_completa — a guia de doc orto
    completa passa a ser coberta (MAYSA/MIRIAN, 28/07)."""
    ex = expande_documentacao(canon_exames(AMANDA_PEDIDO_INTEIRO))
    assert {"panoramica", "telerradiografia", "fotografia", "documentacao"} <= ex
    assert "documentacao_completa" in ex


def test_amanda_queiroz_so_a_secao_radiografica_nao_vira_completa():
    """Documenta o BUG e trava a regra contra falso-positivo: com SÓ a seção 1
    (sem as fotos) NÃO há 'fotografia' — a documentação_completa NÃO pode ser
    promovida. Quem promove é a âncora 'fotografia', que só aparece quando o pedido
    REALMENTE tem fotos. Ler a seção das fotos não afrouxa nada."""
    ex = expande_documentacao(canon_exames(AMANDA_SECAO1))
    assert ex == {"panoramica", "telerradiografia"}
    assert "documentacao_completa" not in ex


def test_texto_verbatim_recupera_fotos_mesmo_a_lista_dropando():
    """MAYSA/MIRIAN (28/07, run 269): a IA CUROU exames_lidos=[panoramica, tele,
    ricketes] e DROPOU a secao de fotos — mas o campo 'texto' traz a transcricao
    LITERAL do pedido inteiro. _texto_pedido junta os dois e o canon recupera
    'fotografia' -> documentacao_completa. Prova que a decisao NAO depende de a IA
    escolher certo a lista; depende da transcricao literal, que o CODIGO canoniza.
    (Sem o fix, canonizando so a lista, daria {panoramica, telerradiografia} e a
    guia 'Doc Orto Compl' caia em 'nao cobre'.)"""
    from esteira import _texto_pedido
    a = {"tipo": "solicitacao",
         "paciente_lido": "MAYSA ANTONIA COELHO JESUS DOS SANTOS",
         "exames_lidos": ["PANORAMICA EM TOPO", "TELE PERFIL COM TRACADO ANATOMICO",
                          "RICKETES FATORE"],
         "texto": ("Solicito: Dra. AMANDA QUEIROZ EXAMES RADIOGRAFICOS INTRA BUCAIS "
                   "1-PANORAMICA EM TOPO 2-TELE PERFIL COM TRACADO ANATOMICO "
                   "3-RICKETES FATORE DOCUMENTACAO FOTOS INTRA BUCAIS FRONTAL PERFIL "
                   "DIREITO E ESQUERDO SORRISO FOTOS INTRA BUCAIS FRONTAL PERFIL "
                   "DIREITO E ESQUERDO E ARCOS")}
    # com o texto literal:
    ex = expande_documentacao(canon_exames(_texto_pedido(a)))
    assert {"panoramica", "telerradiografia", "fotografia"} <= ex
    assert "documentacao_completa" in ex
    # sem o texto (so a lista curada) NAO promove — o que causava o bug:
    so_lista = expande_documentacao(canon_exames(" ".join(a["exames_lidos"])))
    assert "documentacao_completa" not in so_lista


def test_escolher_aceita_maysa_pela_transcricao_literal():
    """Fim a fim: guia 'Doc Orto Compl'; a leitura tem exames_lidos SEM fotos, mas
    'texto' com as fotos -> _escolher_solicitacao ACEITA (idx 0, motivo None), porque
    canoniza _texto_pedido. Com a lista sozinha (sem 'texto'), a MESMA guia reprova."""
    a_com_texto = {"idx": 0, "tipo": "solicitacao", "legivel": True,
                   "paciente_lido": "MAYSA ANTONIA COELHO JESUS DOS SANTOS",
                   "exames_lidos": ["PANORAMICA EM TOPO", "TELE PERFIL", "RICKETES FATORE"],
                   "texto": ("EXAMES RADIOGRAFICOS 1-PANORAMICA EM TOPO 2-TELE PERFIL "
                             "COM TRACADO 3-RICKETES DOCUMENTACAO FOTOS INTRA BUCAIS "
                             "FRONTAL PERFIL DIREITO E ESQUERDO SORRISO ARCOS")}
    alvo = canon_exames("Doc Orto Compl")
    idx, _a, motivo = _escolher_solicitacao(
        [a_com_texto], "MAYSA ANTONIA COELHO JESUS DOS SANTOS", alvo, 1)
    assert idx == 0 and motivo is None

    a_so_lista = dict(a_com_texto); a_so_lista.pop("texto")
    idx2, _a2, motivo2 = _escolher_solicitacao(
        [a_so_lista], "MAYSA ANTONIA COELHO JESUS DOS SANTOS", alvo, 1)
    assert idx2 is None and motivo2 == "NAO_COBRE"


def test_amanda_queiroz_maysa_passa_no_escolher_so_com_o_pedido_inteiro():
    """Fim a fim: a guia autoriza documentação ortodôntica COMPLETA ('Doc Orto
    Compl'). Com o pedido inteiro (as duas seções) a solicitação cobre e é aceita;
    com só a seção radiográfica a MESMA guia é reprovada por não cobrir — o que
    dependia unicamente de a leitura capturar a seção das fotos."""
    alvo = canon_exames("Doc Orto Compl")          # {documentacao, documentacao_completa}
    assert "documentacao_completa" in alvo

    inteiro = [_leitura(0, "MAYSA ANTONIA COELHO JESUS DOS SANTOS",
                        [AMANDA_SECAO1, AMANDA_SECAO2])]
    idx, _a, motivo = _escolher_solicitacao(
        inteiro, "MAYSA ANTONIA COELHO JESUS DOS SANTOS", alvo, 1)
    assert idx == 0 and motivo is None

    truncado = [_leitura(0, "MAYSA ANTONIA COELHO JESUS DOS SANTOS", [AMANDA_SECAO1])]
    idx2, _a2, motivo2 = _escolher_solicitacao(
        truncado, "MAYSA ANTONIA COELHO JESUS DOS SANTOS", alvo, 1)
    assert idx2 is None and motivo2 == "NAO_COBRE"


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


# ── Token do laudo pan com DOIS args (mudanca do SmartRIS ~05/08/26) ─────────
# O SmartRIS passou a chamar openReportPDF(event, '<token>', '<x>') com DOIS
# argumentos (antes era so um). O regex exigia ')' logo apos o 1o token
# (…'([^']+)'\)) e, com o 2o arg, casava ZERO — o laudo PRONTO (Validado/Impresso)
# era lido como FALTA LAUDO e a guia virava pendencia. Medido no dia 31/07: ~22
# panoramicas laudadas por dia lidas como sem laudo. arg1 e o token de studies
# (fetch de report/pdf?studies=arg1 devolveu 93KB; arg2 devolveu 0). O ceph
# continua com 1 arg e ja funcionava.

def test_pan_com_dois_args_extrai_o_primeiro():
    from extrator_arquivos import extrair_tokens
    h = ('<td onclick="openReportPDF(event, \'TOKENPAN123\', \'REPORTTYPE\')">'
         '<span class="wrap-exam">PANORAMICA</span></td>')
    assert extrair_tokens(h)["pan"] == ["TOKENPAN123"]


def test_pan_com_um_arg_ainda_extrai():
    # robustez: se o SmartRIS voltar ao formato de 1 arg, continua funcionando
    from extrator_arquivos import extrair_tokens
    h = '<td onclick="openReportPDF(event, \'SOZINHO\')">x</td>'
    assert extrair_tokens(h)["pan"] == ["SOZINHO"]


def test_pan_nao_captura_token_de_ceph():
    # openReportPDF NAO pode casar dentro de openReportPDFCeph (prefixo)
    from extrator_arquivos import extrair_tokens
    h = '<td onclick="openReportPDFCeph(event, \'CEPHTOK\')">x</td>'
    tk = extrair_tokens(h)
    assert tk["pan"] == [] and tk["ceph"] == ["CEPHTOK"]


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


# ── Guia RE-ASSINADA nao e guia documentada (casos PAULO SERGIO, WELLINGHTON,
# FABIO e PATRICK, 27/07) ───────────────────────────────────────────────────
# Uma re-assinatura em lote (21:30 de 27/07) criou a 2ª copia da imagem
# assinada da GTO (imagemGTO=True) nas 4 guias. A regra antiga pulava por
# CONTAGEM (2+ anexos = documentada) e as 4 foram registradas como FATURADAS
# sem ter laudo, solicitacao nem imagem. "Documentada" = existir anexo com
# imagemGTO=False; copia da GTO nao conta. E como os 2 anexos tinham o MESMO
# nome, o set de nomes dizia "ja tinha 1 anexo(s)" no relatorio — foi essa
# incoerencia que entregou o bug.

def _anx(nome, gto_flag=None):
    d = {"id": 1, "nomeArquivo": nome}
    if gto_flag is not None:
        d["imagemGTO"] = gto_flag
    return d


def test_guia_reassinada_nao_conta_como_documentada():
    from esteira import _anexos_portal_split
    copias, docs = _anexos_portal_split(
        [_anx("img_ASSINADA.png", True), _anx("img_ASSINADA.png", True)])
    assert len(copias) == 2 and docs == []


def test_guia_com_documento_de_verdade_continua_pulada():
    from esteira import _anexos_portal_split
    copias, docs = _anexos_portal_split(
        [_anx("img_ASSINADA.png", True),
         _anx("LAUDO_PANORAMICA_40337001_OFICIAL.pdf", False),
         _anx("SOLICITACAO_CANDICE.jpg", False)])
    assert len(copias) == 1 and len(docs) == 2


def test_anexo_sem_flag_conta_como_documento():
    """API sem o campo imagemGTO -> trata como documentada (pula): o lado
    conservador e NAO anexar, porque duplicar e irreversivel."""
    from esteira import _anexos_portal_split
    copias, docs = _anexos_portal_split(
        [_anx("img_ASSINADA.png", True), _anx("misterio.pdf")])
    assert len(copias) == 1 and len(docs) == 1


def test_upload_com_teto_das_copias_da_gto(tmp_path):
    """max_antes = nº de copias da GTO vistas na descoberta. Guia re-assinada
    (2 anexos, ambos copia) pode receber documentacao; qualquer anexo NOVO
    desde a descoberta (3 > 2) bloqueia; e sem o parametro o teto segue 1."""
    import pytest
    from extrator_odontoprev import upload_arquivos
    f = tmp_path / "LAUDO_PANORAMICA_1_OFICIAL.pdf"; f.write_bytes(b"x")
    r = upload_arquivos(_GP(3), [str(f)], max_antes=2)
    assert r["ok"] is False and "ja tem 3 anexos" in r["erro"]
    r = upload_arquivos(_GP(2), [str(f)])
    assert r["ok"] is False and "ja tem 2 anexos" in r["erro"]
    # com teto 2 a trava LIBERA: o fake nao tem input[type=file], entao o fluxo
    # segue ate estourar NELE — prova de que nao parou na trava de duplicidade
    with pytest.raises(RuntimeError):
        upload_arquivos(_GP(2), [str(f)], max_antes=2)


# ── Download de anexo pelo ACERVO DIGITAL (endpoint real, 31/07) ────────────
# Os 4 endpoints antigos davam 404 e o campo 49 da guia baixada do portal nunca
# chegava ao leitor (caso DAVI SANTANA, GTO 195456616: justificativa preenchida
# na guia e o robo pedindo "pedido do dentista"). O endpoint real e
# /v1/gto/acervo-digital/imagem?numeroFicha=&sequencial= e devolve BASE64.

class _SessFake:
    def __init__(self, txt, status=200):
        self._t, self._s, self.url = txt, status, None
    def get(self, url, timeout=0):
        self.url = url
        class _R:  # noqa
            pass
        r = _R(); r.status_code = self._s; r.text = self._t
        return r


def test_download_acervo_digital_decodifica_base64():
    import base64
    from esteira import _baixar_anexo_portal
    png = b"\x89PNG\r\n" + b"\x00" * 600
    s = _SessFake(base64.b64encode(png).decode())
    blob, mime = _baixar_anexo_portal(s, "195456616", 1)
    assert mime == "image/png" and blob == png
    assert "acervo-digital/imagem" in s.url and "sequencial=1" in s.url


def test_download_acervo_digital_rejeita_nao_arquivo():
    """HTML de login (sessao caida) em base64 nao pode virar 'imagem'."""
    import base64
    from esteira import _baixar_anexo_portal
    s = _SessFake(base64.b64encode(b"<html>login</html>" + b" " * 600).decode())
    assert _baixar_anexo_portal(s, "195456616", 1) == (None, "")
    # e sem guia/posicao nao bate na API (contrato antigo preservado)
    assert _baixar_anexo_portal(None, None) == (None, "")


# ── Leitura degenerada do lote (caso SOPHIA, 31/07) ─────────────────────────
# O lote fez o modelo loopar (200k tokens de saida, JSON truncado, 762s) e a
# guia morreu com "gemini: Unterminated string". O resgate le os anexos UM A UM
# e remapeia o idx para a posicao real do lote.

def test_fallback_um_a_um_remapeia_idx():
    from esteira import _ler_anexos_um_a_um

    class _Resp:
        text = '[{"idx": 0, "tipo": "solicitacao", "paciente_lido": "X"}]'
        usage_metadata = None

    class _Gem:
        class models:  # noqa
            @staticmethod
            def generate_content(model=None, contents=None, config=None):
                return _Resp()

    cands = [("a.jpg", "image/jpeg", b"x", "a"), ("b.jpg", "image/jpeg", b"x", "b")]
    ls = _ler_anexos_um_a_um(_Gem, cands)
    assert [a["idx"] for a in ls] == [0, 1]
    assert all(a["tipo"] == "solicitacao" for a in ls)


# ── Pendencia sem dono e pendencia que ninguem pega (31/07) ─────────────────
# Tres motivos reais do dia 27/07 caiam em "Outros/Conferencia": a data vencida
# da ESTER, o "nao encontrado no cadastro" da ANGELICA LEAHY e o erro tecnico
# da SOPHIA — que por ser falha NOSSA tem que ir para a fila tecnica.

def test_novos_motivos_ganham_dono():
    from db import classificar_pendencia
    ch, quem, acao = classificar_pendencia(
        "Solicitação com data vencida e o sistema não localizou onde ajustar "
        "(sem box da data) — revisar")
    assert ch == "data_vencida" and quem == "Conferência" and acao
    ch, quem, _ = classificar_pendencia(
        "anexos: paciente 'ANGELICA OLIVEIRA  LEAHY' não encontrado no "
        "cadastro do PRORADIS — conferir se o nome está escrito igual")
    assert ch == "paciente_nao_achado" and quem == "Cadastro"
    ch, quem, _ = classificar_pendencia(
        "gemini: Unterminated string starting at: line 2267 column 9")
    assert ch == "falha_tecnica" and quem == "Nós"
    ch, quem, _ = classificar_pendencia(
        "NÃO FATUROU porque este paciente tem exame em MAIS DE UM dia próximo "
        "à guia (26/07/2026, 28/07/2026) e o sistema não tem como saber qual "
        "exame pertence a esta guia")
    assert ch == "multi_dia" and quem == "Conferência"


# ── Busca por nome com sobrenome a mais / espaco duplo (JEUSA e ANGELICA) ───
# JEUSA (27/07): a worklist achava 0 com o nome COMPLETO do analitico e achava
# com um sobrenome a menos — o laudo existia, integro (134KB). ANGELICA LEAHY:
# o nome veio com espaco DUPLO e a busca do prontuario achava 0 cards.

def test_tentativas_de_nome_encurtam_ate_dois_tokens():
    from extrator_arquivos import _tentativas_nome
    assert _tentativas_nome("JEUSA OLIVEIRA MATOS ARAUJO") == [
        "JEUSA OLIVEIRA MATOS ARAUJO", "JEUSA OLIVEIRA MATOS", "JEUSA OLIVEIRA"]
    assert _tentativas_nome("ANGELICA OLIVEIRA  LEAHY") == [
        "ANGELICA OLIVEIRA LEAHY", "ANGELICA OLIVEIRA"]  # espaco duplo colapsado
    assert _tentativas_nome("MARIA SILVA") == ["MARIA SILVA"]  # 2 tokens: nao encurta
    assert _tentativas_nome("") == []


# ── Nome manuscrito com DOIS typos pequenos e o "Rx BW" do dentista (caso
# SOPHIA CARVALHO DO ROSARIO, GTO 195469193, 27/07 Tancredo) ────────────────
# O modelo leu o pedido BEM: paciente 'Sophia Carvallo do Rosamo' (2 typos de
# grafia), dentista 'Dra. Erica Lima' CRO 8334 (o carimbo), exames [Panorâmica,
# Rx Bw direito e esquerdo]. Quem reprovou foram as NOSSAS reguas: (a) a
# identidade exigia 2 tokens IDENTICOS e typo contava como divergente ->
# "outra pessoa"; (b) "BW" (bitewing) nao estava no vocabulario -> "nao cobre".

def test_caso_sophia_dois_typos_de_grafia_casam():
    assert _nomes_compat("Sophia Carvallo do Rosamo", "SOPHIA CARVALHO DO ROSARIO")


def test_pareamento_de_grafia_nao_abre_para_parente():
    # tokens realmente DIFERENTES nao pareiam por grafia
    assert not _nomes_compat("PEDRO SILVA SANTOS", "JOAO SILVA SANTOS")
    assert not _nomes_compat("THAILAN CABRAL SALLES DE ALMEIDA",
                             "TAINA SALLES DE OLIVEIRA")
    # e sem NENHUM token identico nao ha pareamento que salve
    assert not _nomes_compat("MARIA SOUSA", "MARIO SOUZA")


def test_rx_bw_e_interproximal():
    from solicitacao_utils import canon_exames
    ex = canon_exames("Panorâmica em topo. Rx BW direito e esquerdo (PM e M)")
    assert {"panoramica", "interproximal"} <= ex
    # 'bw' precisa de fronteira de palavra: nao pode nascer de outra palavra
    assert "interproximal" not in canon_exames("subwoofer")


def test_caso_sophia_passa_inteiro():
    l = _leitura_dt(0, "Sophia Carvallo do Rosamo",
                    ["Panorâmica", "Rx Bw direito e esquerdo"], "21/07/2026")
    idx, _a, motivo = _escolher_solicitacao(
        [l], "SOPHIA CARVALHO DO ROSARIO", {"panoramica", "interproximal"}, 1)
    assert motivo is None and idx == 0


# ── Prontuario DUPLICADO no PRORADIS (caso IRAMAIA MACIEL LOPES DE SOUZA,
# GTO 195441968, 27/07) ─────────────────────────────────────────────────────
# A paciente tem DOIS prontuarios (20053338 e 20031156) com o mesmo nome e o
# mesmo nascimento (25/03/1978). O exame estava no prontuario que o analitico
# aponta e o PEDIDO foi anexado no duplicado — o robo lia so o primeiro e
# reprovava com "nenhum documento no nome deste paciente". Duplicata provada
# (nome + nascimento identicos) agora e lida tambem.

def test_prontuarios_duplicados_sao_gemeos():
    from extrair_anexos_dia import _gemeos_de
    cards = [
        {"href": "H1", "nome": "IRAMAIA MACIEL LOPES DE SOUZA",
         "nascimento": "25/03/1978", "cod": "20053338"},
        {"href": "H2", "nome": "IRAMAIA MACIEL LOPES DE SOUZA",
         "nascimento": "25/03/1978", "cod": "20031156"},
        {"href": "", "nome": "IRAMAIA MACIEL LOPES DE SOUZA",
         "nascimento": ""},   # card "+ Novo Paciente" (sem nascimento)
    ]
    g = _gemeos_de(cards, "H1")
    assert [c["cod"] for c in g] == ["20031156"]


def test_gemeo_exige_nome_E_nascimento_identicos():
    from extrair_anexos_dia import _gemeos_de
    cards = [
        {"href": "H1", "nome": "MARIA SILVA", "nascimento": "01/01/1990"},
        {"href": "H2", "nome": "MARIA SILVA", "nascimento": "02/02/1985"},        # homonima real
        {"href": "H3", "nome": "MARIA SILVA SANTOS", "nascimento": "01/01/1990"}, # outro nome
    ]
    assert _gemeos_de(cards, "H1") == []
    # principal sem nascimento: nao ha como PROVAR duplicidade -> nao le nada extra
    assert _gemeos_de([{"href": "H1", "nome": "X Y", "nascimento": ""},
                       {"href": "H2", "nome": "X Y", "nascimento": ""}], "H1") == []


# ── Mensagem "nao cobre" tem de descrever o MESMO candidato (caso MARIA CLARA,
# GTO 195436162, 27/07) ─────────────────────────────────────────────────────
# Quando o candidato mais recente teve exames ilegiveis (ex vazio), a mensagem
# antiga mostrava "FALTA periapical" (do candidato avaliado) junto com "o pedido
# pede [...periapical...]" (de OUTRO anexo) — contradicao pura. det["lidos"] e
# det["falta"] tem de sair do MESMO candidato avaliado.

def test_det_lidos_e_falta_sao_do_mesmo_candidato():
    # so ha UMA solicitacao compat, e seus exames nao canonizam em nada
    l = _leitura(0, "MARIA CLARA DA SILVA SOUZA", ["texto ilegivel xyz"])
    det = {}
    idx, _a, motivo = _escolher_solicitacao(
        [l], "MARIA CLARA DA SILVA SOUZA", {"periapical"}, 1, detalhe=det)
    assert idx is None and motivo == "NAO_COBRE"
    # o candidato avaliado nao leu exame nenhum -> lidos vazio, falta = alvo
    # inteiro; as duas descrevem o MESMO anexo (nada de "falta X mas pede X")
    assert det["lidos"] == []
    assert set(det["falta"]) == {"periapical"}


# ── Releitura focada do box da data (caso ESTER SANTOS EISENBACH, GTO 195441738,
# 27/07) ────────────────────────────────────────────────────────────────────
# Pedido validado + data vencida, mas a IA nao devolveu ONDE a data esta. Em vez
# de mandar direto pra revisao manual, o sistema pergunta so a localizacao, num
# anexo so — acerta muito mais.

def test_releitura_prioriza_anexo_no_nome_do_paciente():
    """Caso MARIA CLARA (GTO 195436162): prontuario com muitos anexos; os pedidos
    reais dela (no nome dela, com exames) estavam DEPOIS dos documentos genericos
    e o teto de 4 releituras nunca os alcancava. A reordenacao poe (nome-compat +
    exames) na frente."""
    from esteira import _reler_nao_classificados

    # blob = índice do anexo (byte), pra sabermos qual foi relido
    cands = [("f%d.jpg" % i, "image/jpeg", bytes([i]), None) for i in range(6)]
    leituras = [
        {"idx": 0, "tipo": "documento", "paciente_lido": "", "exames_lidos": []},
        {"idx": 1, "tipo": "documento", "paciente_lido": "", "exames_lidos": []},
        {"idx": 2, "tipo": "documento", "paciente_lido": "", "exames_lidos": []},
        {"idx": 3, "tipo": "documento", "paciente_lido": "", "exames_lidos": []},
        {"idx": 4, "tipo": "documento", "paciente_lido": "MARIA CLARA DA SILVA SOUZA",
         "exames_lidos": ["RX PANORAMICA", "PERIAPICAL"]},
        {"idx": 5, "tipo": "documento", "paciente_lido": "MARIA CLARA DA SILVA SOUZA",
         "exames_lidos": ["RX PANORAMICA", "PERIAPICAL"]},
    ]
    relidos = []

    def _gen(model=None, contents=None, config=None):
        # o 1o item de contents é o Part.from_bytes; recupera o byte-índice
        for p in contents:
            data = getattr(getattr(p, "inline_data", None), "data", None)
            if data:
                relidos.append(data[0])
        class _R:
            text = '{"tipo": "documento"}'
            usage_metadata = None
        return _R()

    class _G:
        class models:  # noqa
            generate_content = staticmethod(_gen)

    _reler_nao_classificados(_G, cands, leituras, max_reler=4,
                             nome_gto="MARIA CLARA DA SILVA SOUZA")
    # teto 4: os DOIS anexos no nome dela (idx 4 e 5) TÊM de ter sido relidos,
    # mesmo estando por último na ordem do prontuário
    assert 4 in relidos and 5 in relidos
    assert len(relidos) == 4


def test_reler_box_data_devolve_a_caixa():
    from esteira import _reler_box_data

    class _Resp:
        text = '{"data_solicitacao": "10/01/2026", "box_data": [820, 400, 860, 700]}'
        usage_metadata = None

    class _Gem:
        class models:  # noqa
            @staticmethod
            def generate_content(model=None, contents=None, config=None):
                return _Resp()

    cands = [("pedido.jpg", "image/jpeg", b"x", None)]
    box, data = _reler_box_data(_Gem, cands, 0)
    assert box == [820.0, 400.0, 860.0, 700.0] and data == "10/01/2026"
    # idx fora da lista: nao chama o modelo, devolve (None, None)
    assert _reler_box_data(_Gem, cands, 5) == (None, None)


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


# ── Corroboração do prontuário quando o nome está ILEGÍVEL (caso MAYSA ANTONIA
# COELHO JESUS DOS SANTOS, GTO 195516236, 28/07 Centro) ──────────────────────
# Pedido manuscrito de criança: o nome sai "Maja"/"Maysa" (1 token) e o dentista
# ora "Mylena Queiroz" ora "Amanda Queiroz" — leitura cara-ou-coroa. A GTO da
# própria guia está no prontuário dela (aberto pelo cadastro exato), o que prova
# que o prontuário é dela. Nível "Equilibrado": aceita nome ilegível se o
# prontuário está confirmado E o dentista NÃO contradiz (só rejeita dentista
# claramente OUTRO) E os exames cobrem.

def test_dentista_contradiz():
    from esteira import _dentista_contradiz
    # sobrenome em comum (Queiroz) -> NÃO contradiz: recupera a MAYSA
    assert not _dentista_contradiz({"dentista_lido": "Amanda Queiroz", "cro_lido": ""},
                                   "MYLENA SILVA QUEIROZ SANTANA")
    # dentista claramente OUTRO (2 tokens, zero em comum) -> contradiz
    assert _dentista_contradiz({"dentista_lido": "Carlos Andrade Souza", "cro_lido": ""},
                               "MYLENA SILVA QUEIROZ SANTANA")
    # ilegível/vazio -> não contradiz (ausência de evidência)
    assert not _dentista_contradiz({"dentista_lido": "", "cro_lido": ""},
                                   "MYLENA SILVA QUEIROZ SANTANA")
    # um token só (leitura parcial) -> não contradiz
    assert not _dentista_contradiz({"dentista_lido": "Mylena", "cro_lido": ""},
                                   "MYLENA SILVA QUEIROZ SANTANA")
    # CRO bate -> confirma, não contradiz, mesmo com nome diferente
    assert not _dentista_contradiz({"dentista_lido": "Nome Errado", "cro_lido": "12345"},
                                   "OUTRO DENTISTA", "CRO 12345")


def test_maysa_passa_por_corroboracao_do_prontuario():
    l = _leitura2(0, "Maja",
                  ["PANORAMICA EM TOPO", "TELE PERFIL COM TRACADO", "RICKETES",
                   "FOTOS INTRA BUCAIS", "MODELO"],
                  dentista="Amanda Queiroz")
    idx, _a, mot = _escolher_solicitacao(
        [l], "MAYSA ANTONIA COELHO JESUS DOS SANTOS", {"documentacao_completa"}, 1,
        "MYLENA SILVA QUEIROZ SANTANA", prontuario_confirmado=True)
    assert idx == 0 and mot is None


def test_corroboracao_rejeita_pedido_de_dentista_diferente():
    """Trava de família: mesmo com o prontuário confirmado, um pedido cujo
    dentista legível é OUTRO (2 tokens, nenhum em comum) é rejeitado."""
    l = _leitura2(0, "Jaja", ["panoramica"], dentista="CARLOS ANDRADE SOUZA")
    idx, _a, motivo = _escolher_solicitacao(
        [l], "MAYSA ANTONIA COELHO JESUS DOS SANTOS", {"panoramica"}, 1,
        "MYLENA SILVA QUEIROZ SANTANA", prontuario_confirmado=True)
    assert idx is None and motivo == "PACIENTE_INCOMPATIVEL"


def test_corroboracao_rejeita_com_dois_pedidos_ilegiveis():
    """Trava de FAMÍLIA (achado do code review): prontuário confirmado, mas DOIS
    pedidos de nome ilegível — o do paciente e o de um irmão mal-arquivado, do
    MESMO dentista da família (que não contradiz). Não dá para distinguir qual é
    de quem → vai para revisão, nunca chuta. A corroboração só vale quando o
    pedido ilegível é ÚNICO."""
    l0 = _leitura2(0, "Maja", ["panoramica"], dentista="Amanda Queiroz")
    l1 = _leitura2(1, "Lu", ["panoramica"], dentista="Amanda Queiroz")
    idx, _a, motivo = _escolher_solicitacao(
        [l0, l1], "MAYSA ANTONIA COELHO JESUS DOS SANTOS", {"panoramica"}, 2,
        "MYLENA SILVA QUEIROZ SANTANA", prontuario_confirmado=True)
    assert idx is None and motivo == "PACIENTE_INCOMPATIVEL"


def test_corroboracao_exige_prontuario_confirmado():
    """Sem prontuário confirmado, nome ilegível + dentista que não bate (só
    'Queiroz' em comum) continua indo para revisão, como hoje."""
    l = _leitura2(0, "Maja", ["panoramica"], dentista="Amanda Queiroz")
    idx, _a, motivo = _escolher_solicitacao(
        [l], "MAYSA ANTONIA COELHO JESUS DOS SANTOS", {"panoramica"}, 1,
        "MYLENA SILVA QUEIROZ SANTANA", prontuario_confirmado=False)
    assert idx is None and motivo == "PACIENTE_INCOMPATIVEL"


def test_corroboracao_nao_afrouxa_nome_de_outra_pessoa_legivel():
    """Nome LEGÍVEL de outra pessoa (parente) continua rejeitado mesmo com o
    prontuário confirmado — a corroboração é SÓ para nome ilegível."""
    l = _leitura2(0, "THAILAN CABRAL SALLES DE ALMEIDA", ["panoramica"],
                  dentista="VIRGINIA GABRIELA OLIVEIRA ALMEIDA")
    idx, _a, _m = _escolher_solicitacao(
        [l], "TAINA SALLES DE OLIVEIRA", {"panoramica"}, 1,
        "VIRGINIA GABRIELA OLIVEIRA ALMEIDA", prontuario_confirmado=True)
    assert idx is None


# ── Nome MAL LIDO da mesma pessoa (caso SIDNEY, 27/07) ───────────────────────
# "Sidney Sortas auto" (o Gemini leu mal) compartilha o PRIMEIRO nome com
# "SIDNEY SANTOS CARVALHO" e o sobrenome saiu ilegível (SORTAS≈SANTOS). Antes o
# código tratava "1 token em comum = PARENTE" e rejeitava, e a guia caía num
# pedido velho de 2024. Distinção segura: mesma pessoa mal lida compartilha o
# PRIMEIRO nome; parente/irmão compartilha o SOBRENOME e tem o 1º nome DIFERENTE.

def test_erro_de_leitura_do_nome():
    from esteira import _erro_de_leitura_do_nome
    # SIDNEY: 1º nome corresponde, sobrenome corrompido (SORTAS~SANTOS), "auto" é lixo -> mal lido
    assert _erro_de_leitura_do_nome("Sidney Sortas auto", "SIDNEY SANTOS CARVALHO")
    # irmão: mesmo sobrenome, 1º nome LIMPO e diferente -> outra pessoa
    assert not _erro_de_leitura_do_nome("MARCOS SANTOS CARVALHO", "SIDNEY SANTOS CARVALHO")
    # vários nomes limpos e diferentes (SALLES pai×filho) -> outra pessoa
    assert not _erro_de_leitura_do_nome("THAILAN CABRAL SALLES ALMEIDA", "TAINA SALLES DE OLIVEIRA")
    # nada corresponde -> não é corrupção (é ausência, tratada à parte)
    assert not _erro_de_leitura_do_nome("", "SIDNEY SANTOS CARVALHO")
    # FURO DO CODE REVIEW: irmã de primeiro nome CURTO (ANA, EVA) NÃO pode passar
    # como "mal lido" — 1º nome que não corresponde = outra pessoa, qualquer tamanho.
    assert not _erro_de_leitura_do_nome("ANA SANTOS CARVALHO", "SIDNEY SANTOS CARVALHO")
    assert not _erro_de_leitura_do_nome("EVA SILVA LIMA", "SIDNEY SILVA LIMA")
    assert not _erro_de_leitura_do_nome("ANA SILVA", "JOSE SILVA")


def test_recencia_nao_entrega_guia_para_irma_de_nome_curto():
    """Furo do review: irmã 'ANA' (idx 0, mais recente) NÃO pode ganhar do pedido
    correto do próprio paciente (idx 1). 'ANA' não é leitura ruim de 'SIDNEY'."""
    irma = _leitura2(0, "ANA SANTOS CARVALHO", ["periapical"], dentista="Vanessa Teixeira Gadea")
    dele = _leitura2(1, "SIDNEY SANTOS CARVALHO", ["periapical"], dentista="Vanessa Teixeira Gadea")
    idx, _a, _m = _escolher_solicitacao(
        [irma, dele], "SIDNEY SANTOS CARVALHO", {"periapical"}, 2, prontuario_confirmado=True)
    assert idx == 1  # o pedido do próprio paciente, não o da irmã


def test_sidney_nome_mal_lido_aceita_por_corroboracao():
    """1º nome bate + prontuário confirmado + único ambíguo + dentista não
    contradiz + cobre → aceita, apesar do sobrenome mal lido."""
    l = _leitura2(0, "Sidney Sortas auto", ["periapical"], dentista="Vanessa Teixeira Gadea")
    idx, _a, mot = _escolher_solicitacao(
        [l], "SIDNEY SANTOS CARVALHO", {"periapical"}, 1, prontuario_confirmado=True)
    assert idx == 0 and mot is None


def test_sidney_irmao_mesmo_sobrenome_primeiro_nome_diferente_rejeita():
    """Trava do irmão: mesmo com prontuário confirmado, primeiro nome DIFERENTE
    (compartilha só o sobrenome) continua rejeitado — não é a mesma pessoa."""
    l = _leitura2(0, "MARCOS SANTOS CARVALHO", ["periapical"], dentista="Vanessa Teixeira Gadea")
    idx, _a, motivo = _escolher_solicitacao(
        [l], "SIDNEY SANTOS CARVALHO", {"periapical"}, 1, prontuario_confirmado=True)
    assert idx is None and motivo == "PACIENTE_INCOMPATIVEL"


def test_sidney_dois_mal_lidos_do_mesmo_primeiro_nome_vao_a_revisao():
    """Dois pedidos ambíguos (nome mal lido) → não dá para distinguir → revisão."""
    l0 = _leitura2(0, "Sidney Sortas auto", ["periapical"], dentista="Vanessa Teixeira Gadea")
    l1 = _leitura2(1, "Sidney Xyzt uvwq", ["periapical"], dentista="Vanessa Teixeira Gadea")
    idx, _a, motivo = _escolher_solicitacao(
        [l0, l1], "SIDNEY SANTOS CARVALHO", {"periapical"}, 2, prontuario_confirmado=True)
    assert idx is None and motivo == "PACIENTE_INCOMPATIVEL"


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


# ── Renovacao da sessao sticky do proxy (03/08) ──────────────────────────────
# A sessao sticky do FlameProxies ('-session-<x>-time-<n>') EXPIRA em <n> min. O
# _fresh_sessid so renovava o formato do DataImpulse (';sessid.<x>') — a sessao do
# FlameProxies ficava fixa e, apos <n> min, TODO run falhava com ERR_TUNNEL,
# obrigando a regerar o proxy a mao. Agora renova os dois formatos: cada run pega
# uma sessao nova, com sua propria janela (set-once-esquece).

def test_fresh_sessid_renova_flameproxies():
    from extrator_odontoprev import _fresh_sessid
    u = ("flma75147f1-package-standard-country-br-city-salvador-"
         "session-278kto37sf-time-50")
    a = _fresh_sessid(u)
    assert a != u and "278kto37sf" not in a         # trocou a sessao
    assert "-session-" in a and "-time-50" in a      # manteve a estrutura
    assert _fresh_sessid(u) != _fresh_sessid(u)      # nova a cada chamada


def test_fresh_sessid_renova_dataimpulse_e_ignora_o_resto():
    from extrator_odontoprev import _fresh_sessid
    d = _fresh_sessid("user;sessid.velho")
    assert d.startswith("user;sessid.") and "velho" not in d
    assert _fresh_sessid("sem_token_de_sessao") == "sem_token_de_sessao"


# ── Recontagem de anexos na anexacao (Classe B, 28/07) ───────────────────────
# JOSE GONCALVES, LEONARDO, SUELEM, RAFAEL, MARIA SOPHIA (run 263): documentacao
# OK, mas a recontagem de anexos pelo DOM (regex 'total de anexos)') devolveu -1
# e a trava — corretamente conservadora — recusou o envio. O retry (6x) ja existe
# e nao resolveu: o DOM renderiza diferente. O conserto e ter uma SEGUNDA fonte
# autoritativa (a API /v1/gto/imagens que a descoberta ja confia) SO como fallback
# quando o DOM falha. _reconta_anexos e a decisao pura desse fallback, e a
# propriedade critica que ela trava: se as DUAS fontes falharem, o resultado e -1
# (bloqueia), nunca uma contagem frouxa — anexar em duplicidade e irreversivel.

def test_reconta_usa_dom_quando_valido_e_nao_chama_api():
    from esteira import _reconta_anexos
    chamou = {"api": False}
    def api_fn():
        chamou["api"] = True
        return (99, None)
    n, fonte, err = _reconta_anexos(2, api_fn)
    assert (n, fonte, err) == (2, "DOM", None)
    assert chamou["api"] is False   # DOM valido nao gasta chamada de API


def test_reconta_zero_do_dom_e_valido():
    """Guia recem-nascida pode ter 0 anexos alem da GTO — 0 e leitura valida,
    nao falha. Nao pode cair no fallback."""
    from esteira import _reconta_anexos
    n, fonte, err = _reconta_anexos(0, lambda: (5, None))
    assert (n, fonte) == (0, "DOM")


def test_reconta_cai_na_api_quando_dom_falha():
    from esteira import _reconta_anexos
    n, fonte, err = _reconta_anexos(-1, lambda: (3, None))
    assert (n, fonte, err) == (3, "API", None)


def test_reconta_bloqueia_quando_dom_e_api_falham():
    """As duas fontes falharam -> -1, que a trava bloqueia. NUNCA devolve uma
    contagem positiva 'chutada' — duplicar anexo e permanente."""
    from esteira import _reconta_anexos
    n, fonte, err = _reconta_anexos(-1, lambda: (-1, "HTTP 500 'erro'"))
    assert n == -1
    assert "HTTP 500" in (err or "")


def test_reconta_excecao_no_fallback_bloqueia():
    """Se a chamada de API estourar, o fallback nao pode propagar nem liberar:
    tem de virar -1 (bloqueia) e registrar o erro."""
    from esteira import _reconta_anexos
    def api_fn():
        raise RuntimeError("timeout")
    n, fonte, err = _reconta_anexos(-1, api_fn)
    assert n == -1
    assert "timeout" in (err or "")


# ── Fallback de contagem+nomes no PONTO DE ESCRITA (Classe B, upload_arquivos) ─
# upload_arquivos (o UNICO ponto de escrita) relia a contagem/nomes de anexos SO
# pelo DOM e bloqueava em antes<0. As 5 guias de 28/07 tinham o DOM quebrado de
# forma persistente -> presas aqui mesmo com a trava da esteira ja liberada.
# _resolver_contagem faz o DOM ser primario e a API (injetada) o fallback
# autoritativo. GUARDRAIL: se DOM falha E fallback falha (ou ausente), devolve o
# dom_n<0 -> as travas de upload bloqueiam. Nunca envia sem contagem confirmada.

def test_resolver_usa_dom_quando_valido_e_nao_chama_fallback():
    from extrator_odontoprev import _resolver_contagem
    chamou = {"fb": False}
    def fb():
        chamou["fb"] = True
        return (99, {"x"})
    n, nomes = _resolver_contagem(2, {"a"}, fb)
    assert (n, nomes) == (2, {"a"})
    assert chamou["fb"] is False


def test_resolver_zero_do_dom_e_valido():
    from extrator_odontoprev import _resolver_contagem
    n, nomes = _resolver_contagem(0, set(), lambda: (5, {"y"}))
    assert n == 0


def test_resolver_cai_no_fallback_quando_dom_falha():
    from extrator_odontoprev import _resolver_contagem
    n, nomes = _resolver_contagem(-1, set(), lambda: (3, {"LAUDO_X.pdf", "img_ASSINADA.png"}))
    assert n == 3
    assert nomes == {"LAUDO_X.pdf", "img_ASSINADA.png"}


def test_resolver_bloqueia_quando_dom_e_fallback_falham():
    from extrator_odontoprev import _resolver_contagem
    n, nomes = _resolver_contagem(-1, {"dom"}, lambda: (-1, set()))
    assert n == -1   # <0 -> upload_arquivos bloqueia


def test_resolver_excecao_no_fallback_bloqueia():
    from extrator_odontoprev import _resolver_contagem
    def fb():
        raise RuntimeError("timeout API")
    n, nomes = _resolver_contagem(-1, {"dom"}, fb)
    assert n == -1


def test_resolver_sem_fallback_mantem_bloqueio():
    from extrator_odontoprev import _resolver_contagem
    n, nomes = _resolver_contagem(-1, {"dom"}, None)
    assert n == -1


# ── Mensagem "nenhum documento no nome" (Classe C, 28/07) ─────────────────────
# A headline PACIENTE_INCOMPATIVEL dizia "nenhum documento do prontuario esta no
# nome deste paciente" mesmo quando havia um RG com o nome IDENTICO da guia — o
# matcher de nome so olha anexos tipo 'solicitacao', mas a MENSAGEM afirmava sobre
# TODOS. Caso CARINA (RG exato, solicitacao lida garbled). _ha_leitura_no_nome so
# conserta o TEXTO: nao muda a escolha nem libera anexo (a guia continua pendencia
# por cobertura). Distingue CARINA (mensagem falsa) de OSNIR (mensagem correta,
# cadastro realmente divergente).

def test_ha_leitura_no_nome_pega_documento_identico():
    from esteira import _ha_leitura_no_nome
    leituras = [
        _leitura(0, "Cavina de jesus des Soures", ["periapical"], tipo="solicitacao"),
        _leitura(1, "CARINA DE JESUS DOS SANTOS", [], tipo="documento"),
        _leitura(2, "Carina de Jesus dos Santos", [], tipo="documento"),
    ]
    assert _ha_leitura_no_nome(leituras, "CARINA DE JESUS DOS SANTOS") is True


def test_ha_leitura_no_nome_falso_quando_cadastro_diverge():
    """OSNIR: nomes lidos 'Osmar'/'OSMAR CORDEIRO DOS SANTOS' — divergencia REAL,
    nao OCR. A headline 'nenhum documento no nome' esta certa -> helper da False."""
    from esteira import _ha_leitura_no_nome
    leituras = [
        _leitura(0, "Osmar", ["panoramica"], tipo="solicitacao"),
        _leitura(1, "Osmar Cunha da Silva", ["periapical"], tipo="solicitacao"),
        _leitura(2, "OSMAR CORDEIRO DOS SANTOS", [], tipo="documento"),
    ]
    assert _ha_leitura_no_nome(leituras, "OSNIR COELHO DOS SANTOS") is False


def test_ha_leitura_no_nome_sem_nome_guia_ou_sem_leituras():
    from esteira import _ha_leitura_no_nome
    assert _ha_leitura_no_nome([_leitura(0, "Fulano de Tal", [])], "") is False
    assert _ha_leitura_no_nome([], "CARINA DE JESUS DOS SANTOS") is False
    assert _ha_leitura_no_nome(None, "CARINA DE JESUS DOS SANTOS") is False
