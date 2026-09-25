"""Escolha do pedido do dentista no Hapvida. Gemini FALSO: devolve leituras prontas;
o que se testa é a regra do código (quem escolhe é o código, não a IA)."""
import json

from PIL import Image

import hapvida_decisao as hd
import hapvida_pedido as hpe
from esteira import _DECISAO_PROMPT

NOME = "MARIA DA SILVA SANTOS"
PAN, PERI, LEV = ["81000405"], ["81000421"], ["81000294"]


class _Resp:
    def __init__(self, txt):
        self.text, self.usage_metadata = txt, None


class GemFake:
    """Lote -> as leituras dadas. Releituras individuais -> '{}' (não acrescentam)."""
    def __init__(self, leituras):
        self.leituras, self.models, self.chamadas = leituras, self, 0

    def generate_content(self, model, contents, config):
        self.chamadas += 1
        if contents and contents[-1] == _DECISAO_PROMPT:
            return _Resp(json.dumps({"anexos": self.leituras}))
        return _Resp("{}")


def _imgs(tmp_path, n, nome="NAME20260920_101010.png"):
    out = []
    for i in range(n):
        p = tmp_path / f"{i:02d}_{nome}"
        Image.new("RGB", (60, 80), "white").save(p, "PNG")
        out.append(str(p))
    return out


def _leitura(idx, **kw):
    a = {"idx": idx, "tipo": "solicitacao", "legivel": True, "paciente_lido": NOME,
         "dentista_lido": "", "cro_lido": "", "exames_lidos": ["panoramica"],
         "texto": "Solicito radiografia panoramica", "data_solicitacao": "20/09/2026"}
    a.update(kw)
    return a


def _rodar(tmp_path, leituras, codigos=PAN, n=None, dia="23/09/2026"):
    arqs = _imgs(tmp_path, n if n is not None else max(1, len(leituras)))
    return hpe.escolher_pedido(GemFake(leituras), arqs, NOME, codigos, dia, str(tmp_path / "out"))


def test_guia_que_nao_exige_pedido_nem_chama_o_gemini(tmp_path):
    gem = GemFake([])
    p = hpe.escolher_pedido(gem, _imgs(tmp_path, 1), NOME, PERI, "23/09/2026", str(tmp_path))
    assert p.arquivos == [] and not p.motivo and gem.chamadas == 0


def test_pedido_do_paciente_que_cobre_e_escolhido(tmp_path):
    p = _rodar(tmp_path, [_leitura(0)])
    assert p.ok and len(p.arquivos) == 1 and p.arquivos[0].endswith(".png")
    assert hd.arquivo_aceito(p.arquivos[0])[0]


def test_o_mais_recente_vence(tmp_path):
    """idx menor = mais recente. O antigo cobre, o novo também: vai o novo."""
    p = _rodar(tmp_path, [_leitura(0, data_solicitacao="20/09/2026"),
                          _leitura(1, data_solicitacao="01/03/2026")])
    assert p.ok and "PEDIDO_0_" in p.arquivos[0] and p.data == "20/09/2026"


def test_pedido_antigo_vira_pendencia_da_clinica(tmp_path):
    p = _rodar(tmp_path, [_leitura(0, data_solicitacao="10/05/2026")])
    assert not p.ok and p.responsavel == "Clínica" and "10/05/2026" in p.motivo


def test_pedido_de_outra_pessoa_nao_e_aceito(tmp_path):
    p = _rodar(tmp_path, [_leitura(0, paciente_lido="JOAO CARLOS PEREIRA")])
    assert not p.ok and p.responsavel == "Conferência"


def test_prontuario_sem_pedido_culpa_a_clinica(tmp_path):
    p = _rodar(tmp_path, [_leitura(0, tipo="documento"), _leitura(1, tipo="laudo")])
    assert not p.ok and p.responsavel == "Clínica" and "entre os 2 documentos" in p.motivo


def test_pedido_que_nao_cobre_diz_o_que_falta(tmp_path):
    p = _rodar(tmp_path, [_leitura(0, exames_lidos=["periapical"], texto="periapical do 36")])
    assert not p.ok and p.responsavel == "Clínica" and "panoramica" in p.motivo


def test_levantamento_escrito_cobre_a_guia(tmp_path):
    p = _rodar(tmp_path, [_leitura(0, exames_lidos=[], texto="Solicito levantamento radiografico")], LEV)
    assert p.ok


def test_so_periapical_nao_cobre_levantamento(tmp_path):
    p = _rodar(tmp_path, [_leitura(0, exames_lidos=["periapical"], texto="periapical do 36")], LEV)
    assert not p.ok and "levantamento" in p.motivo


def test_duas_folhas_do_mesmo_pedido_vao_juntas(tmp_path):
    """pan numa folha, doc na outra, mesma data e mesma dentista -> soma e anexa as duas."""
    lts = [_leitura(0, exames_lidos=["panoramica"], texto="panoramica", cro_lido="1234"),
           _leitura(1, exames_lidos=["documentacao ortodontica"], texto="documentacao ortodontica",
                    cro_lido="1234")]
    p = _rodar(tmp_path, lts, ["81000405", "81000553"])
    assert p.ok and len(p.arquivos) == 2


def test_prontuario_vazio(tmp_path):
    p = hpe.escolher_pedido(GemFake([]), [], NOME, PAN, "23/09/2026", str(tmp_path))
    assert not p.ok and p.responsavel == "Clínica"


def test_decisao_anexa_entregavel_e_pedido_e_repassa_motivo(tmp_path):
    guia = {"guia": "1", "nome": NOME, "dia": "23/09/2026", "itens": [{"codigo": "81000405"}]}
    pac = {"nome": NOME, "nascimento": ""}
    ent = _imgs(tmp_path, 1, "ENTREGA_x.jpg")
    pr = {"encontrado": True, "nome": NOME, "entregaveis": ent, "pedido": ["/p/PEDIDO_0.png"]}
    d = hd.decidir_guia(guia, False, pac, pr)
    assert d.faturavel and d.anexar == ent + ["/p/PEDIDO_0.png"]
    pr = {"encontrado": True, "nome": NOME, "entregaveis": ent, "pedido": None,
          "pedido_motivo": "O pedido é antigo.", "pedido_responsavel": "Clínica"}
    d = hd.decidir_guia(guia, False, pac, pr)
    assert not d.faturavel and d.motivo == "O pedido é antigo."


def _pdf_guia(p):
    import fitz
    d = fitz.open()
    pg = d.new_page()
    pg.insert_text((50, 72), "GUIA DE TRATAMENTO ODONTOLOGICO - GTO  Profissional Solicitante RADIOBRAS")
    pg.insert_text((50, 100), "LEVANTAMENTO RADIOGRAFICO")
    d.save(p)


def test_gto_do_hapvida_no_prontuario_nunca_vira_pedido(tmp_path):
    """Caso ROBERTA (22/09): o prontuário guarda a GTO com o exame escrito e a
    RADIOBRAS como solicitante. Ela sai da lista antes do Gemini."""
    guia = tmp_path / "00_ROBERTA LIMA.pdf"
    _pdf_guia(str(guia))
    gem = GemFake([_leitura(0, texto="levantamento radiografico")])
    p = hpe.escolher_pedido(gem, [str(guia)], NOME, LEV, "22/09/2026", str(tmp_path / "o"))
    assert not p.ok and gem.chamadas == 0


def test_gto_escaneada_lida_como_pedido_e_descartada(tmp_path):
    p = _rodar(tmp_path, [_leitura(0, exames_lidos=[], texto="GUIA DE TRATAMENTO ODONTOLOGICO levantamento radiografico")], LEV)
    assert not p.ok


def test_pedido_vencido_de_levantamento_diz_que_esta_vencido(tmp_path):
    """Caso JESSICA (22/09): o único pedido é de 16/05/2025. O motivo é a data."""
    p = _rodar(tmp_path, [_leitura(0, data_solicitacao="16/05/2025", exames_lidos=["periapical"],
                                   texto="periapical")], LEV, dia="22/09/2026")
    assert not p.ok and "vencida" in p.motivo and "16/05/2025" in p.motivo
