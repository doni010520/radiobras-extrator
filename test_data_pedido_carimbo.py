"""Data do pedido: escrever sempre dentro da folha, cobrir a velha, conferir depois (14/10).
Casos TAISE DE JESUS 198255661 ('08/10/2' cortado) e MARIA DAS GRACAS 198240088
('08/10/2026 0/26' sobreposto)."""
import io
import json

from PIL import Image, ImageDraw, ImageFont

import esteira
from esteira import _carimbar_imagem, _conferir_data_editada


def _img(w=700, h=1000):
    im = Image.new("RGB", (w, h), "white")
    b = io.BytesIO()
    im.save(b, format="JPEG")
    return b.getvalue()


def _escuros(blob):
    im = Image.open(io.BytesIO(blob)).convert("L")
    px = im.load()
    w, h = im.size
    return [(x, y) for x in range(0, w, 2) for y in range(0, h, 2) if px[x, y] < 100]


def test_inserir_na_borda_direita_cabe_na_folha():
    # assinatura colada na borda direita (como a do TAISE DE JESUS)
    out, ok = _carimbar_imagem(_img(), "08/10/2026", "inserir", None, [880, 850, 920, 1000])
    assert ok
    xs = [x for x, _ in _escuros(out)]
    assert xs and max(xs) < 700 - 4          # nada encostado/cortado na borda


def test_atualizar_cobre_a_data_velha_mesmo_com_caixa_imprecisa():
    im = Image.new("RGB", (700, 1000), "white")
    d = ImageDraw.Draw(im)
    f = esteira._fonte_data(30)
    d.text((300, 600), "06/10/26", fill="black", font=f)   # data velha em ~ (300..420, 600..630)
    b = io.BytesIO(); im.save(b, format="JPEG")
    # caixa da leitura deslocada/estreita, como acontece
    out, ok = _carimbar_imagem(b.getvalue(), "08/10/2026", "atualizar", [605, 445, 625, 590], None)
    assert ok
    # nao pode sobrar pixel escuro da data velha FORA da area onde a nova foi escrita
    pts = _escuros(out)
    assert pts
    x0, x1 = min(x for x, _ in pts), max(x for x, _ in pts)
    assert x1 - x0 < 260                       # um bloco so, nao duas datas lado a lado


class _Gem:
    def __init__(self, resposta):
        self.models = self
        self._r = resposta

    def generate_content(self, **k):
        return type("R", (), {"text": json.dumps(self._r), "usage_metadata": None})()


def test_conferencia_aprova_uma_data_igual():
    assert _conferir_data_editada(_Gem({"datas": ["08/10/2026"], "legivel": True}),
                                  b"x", "image/jpeg", "08/10/2026")[0]


def test_conferencia_reprova_duas_datas_ou_ilegivel():
    assert not _conferir_data_editada(_Gem({"datas": ["06/10/2026", "08/10/2026"], "legivel": True}),
                                      b"x", "image/jpeg", "08/10/2026")[0]
    assert not _conferir_data_editada(_Gem({"datas": ["08/10/2026"], "legivel": False}),
                                      b"x", "image/jpeg", "08/10/2026")[0]


def test_pendencia_vai_para_conferencia():
    import db
    m = ("NÃO FATUROU porque, ao ajustar a data do pedido, o pedido ficou com data(s) "
         "06/10/2026, 08/10/2026 em vez de uma única 08/10/2026. Pedido com data ilegível ou "
         "duplicada não pode ser anexado.")
    assert db.classificar_pendencia(m, "revisao")[:2] == ("data_ilegivel", "Conferência")
