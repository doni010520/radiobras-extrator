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
    assert motivo == "solicitacao do paciente nao cobre os exames da GTO"


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
