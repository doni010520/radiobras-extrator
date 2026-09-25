"""Orquestrador Hapvida com portal e PRORADIS falsos — sem rede."""
from PIL import Image

import hapvida_fluxo as hf

LINHAS = [
    {"guia": "1", "usuario": "U1", "nome": "ANA TESTE", "dt_atendimento": "01/09/2026 08:00", "codigo": "81000405"},
    {"guia": "2", "usuario": "U2", "nome": "BRUNO TESTE", "dt_atendimento": "01/09/2026 09:00", "codigo": "81000421"},
    {"guia": "3", "usuario": "U3", "nome": "CARLA TESTE", "dt_atendimento": "01/09/2026 10:00", "codigo": "81000405"},
]


class PortalFake:
    def __init__(self, com_imagem=("3",), confirma=True):
        self.com_imagem = set(com_imagem)
        self.confirma = confirma
        self.envios = []

    def listar_executados(self, dia):
        return LINHAS

    def guia_tem_imagem(self, usuario, guia, dia):
        return guia in self.com_imagem

    def paciente(self, usuario):
        return {"U1": {"nome": "ANA TESTE SILVA", "nascimento": "01/01/1990"},
                "U2": {"nome": "BRUNO TESTE", "nascimento": "02/02/1980"}}[usuario]

    def anexar(self, usuario, guia, arquivos, itens):
        self.envios.append((guia, arquivos, itens))
        if self.confirma:
            self.com_imagem.add(guia)


class FonteFake:
    """Acha todo mundo, com entregável e SEM pedido."""
    def __init__(self, tmp):
        self.tmp = tmp

    def buscar(self, nome, nascimento, dia, codigos, gto=""):
        p = self.tmp / f"ENTREGA_{nome.split()[0]}.jpg"
        Image.new("RGB", (100, 100)).save(p, "JPEG")
        return {"encontrado": True, "ambiguo": False, "nome": nome, "nascimento": nascimento,
                "entregaveis": [str(p)], "pedido": None}


def _rodar(tmp_path, monkeypatch, dry_run=True, real_env=None, portal=None):
    if real_env is None:
        monkeypatch.delenv("HAPVIDA_ANEXAR_REAL", raising=False)
    else:
        monkeypatch.setenv("HAPVIDA_ANEXAR_REAL", real_env)
    portal = portal or PortalFake()
    r = hf.rodar_hapvida("01/09/2026", "centro", portal=portal, fonte=FonteFake(tmp_path),
                         dry_run=dry_run, log=lambda m: None)
    return r, portal, {d["gto"]: d for d in r["decisoes"]}


def test_simulacao_nao_envia_nada(tmp_path, monkeypatch):
    r, portal, d = _rodar(tmp_path, monkeypatch)
    assert portal.envios == [] and r["dry_run"] is True and r["anexado_ok"] == 0
    assert d["3"]["anexado"] == "OK" and d["3"]["categoria"] == "ja_anexada"                        # já tinha imagem
    assert d["1"]["categoria"] == "sem_solicitacao"         # panorâmica sem pedido
    assert d["2"]["anexado"] == "SIMULADO"                  # periapical só com entregável
    assert r["conta"] == "hapvida:centro" and r["plano"] == "hapvida_odonto"


def test_dry_run_false_sem_variavel_continua_simulando(tmp_path, monkeypatch):
    r, portal, _ = _rodar(tmp_path, monkeypatch, dry_run=False, real_env=None)
    assert portal.envios == [] and r["dry_run"] is True


def test_variavel_sem_dry_run_false_continua_simulando(tmp_path, monkeypatch):
    r, portal, _ = _rodar(tmp_path, monkeypatch, dry_run=True, real_env="1")
    assert portal.envios == [] and r["dry_run"] is True


def test_real_so_com_trava_dupla_e_confirma_no_banco(tmp_path, monkeypatch):
    r, portal, d = _rodar(tmp_path, monkeypatch, dry_run=False, real_env="1")
    assert [e[0] for e in portal.envios] == ["2"]
    assert d["2"]["anexado"] == "OK" and r["anexado_ok"] == 1 and r["dry_run"] is False


def test_envio_sem_confirmacao_nao_conta_como_faturado(tmp_path, monkeypatch):
    r, _, d = _rodar(tmp_path, monkeypatch, dry_run=False, real_env="1",
                     portal=PortalFake(confirma=False))
    assert d["2"]["anexado"] is None and d["2"]["categoria"] == "revisao"
    assert r["anexado_ok"] == 0


def test_falha_numa_guia_nao_derruba_as_outras(tmp_path, monkeypatch):
    class Quebra(PortalFake):
        def paciente(self, usuario):
            if usuario == "U1":
                raise RuntimeError("timeout")
            return super().paciente(usuario)
    r, _, d = _rodar(tmp_path, monkeypatch, portal=Quebra())
    assert d["1"]["categoria"] == "erro" and d["2"]["anexado"] == "SIMULADO"


def test_apenas_guias_filtra(tmp_path, monkeypatch):
    monkeypatch.delenv("HAPVIDA_ANEXAR_REAL", raising=False)
    r = hf.rodar_hapvida("01/09/2026", "centro", portal=PortalFake(), fonte=FonteFake(tmp_path),
                         log=lambda m: None, apenas_guias=["2"])
    assert [d["gto"] for d in r["decisoes"]] == ["2"]
