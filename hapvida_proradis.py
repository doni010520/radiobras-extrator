"""
hapvida_proradis.py — Ponte do fluxo Hapvida com o PRORADIS (SmartRIS).

A busca é a MESMA do RedeUna (esteira._baixa_um), só trocando os convênios:
  1. relatório analítico filtrado pelos convênios HAPVIDA -> código real do paciente
     e a procedência de cada exame;
  2. fora do analítico: worklist por nome com _casam_por_paciente/_nomes_compat,
     encurtando o sobrenome e olhando a janela de FATURAR_JANELA_DIAS (7) dias;
     dois pacientes compatíveis ou exame em mais de um dia -> AMBIGUO, não chuta;
  3. nascimento do Hapvida entra no desempate de homônimo (anexos_do_paciente);
  4. esteira._filtrar_arquivos_da_gto tira exame PARTICULAR/de outro convênio do
     mesmo dia. Caso misto: as imagens não dizem de que exame são -> pendência.
Nenhuma função do RedeUna é alterada; só importadas.

CONVÊNIOS: o analítico usa os SEIS convênios HAPVIDA, qualquer que seja a unidade
do login. A guia já vem do portal da unidade; o analítico só serve para achar o
paciente e provar que o exame é do Hapvida — e exame em qualquer convênio HAPVIDA
é exame Hapvida. Assim Lauro/Periperi/Camaçari não somem por mapeamento de login.

Entrega para a decisão: {encontrado, ambiguo, nome, nascimento, entregaveis, laudos,
pedido, pedido_candidatos, misto, exames_fora, data_exame_real}.

PEDIDO DO DENTISTA: só para guia que exige (hd.EXIGE_PEDIDO) e que já tem
entregável — sem entregável a guia não fatura mesmo, e o Gemini não é chamado à toa.
A escolha fica em hapvida_pedido.escolher_pedido. Sem GEMINI_API_KEY, a guia vira
pendência "leitura indisponível" (responsável: Nós), nunca anexo sem pedido.
"""
from __future__ import annotations

import os
import re
import tempfile

import requests
from playwright.sync_api import sync_playwright

import hapvida_decisao as hd
import hapvida_pedido as hpe
from esteira import _baixa_um, _build_by_norm, _filtrar_arquivos_da_gto
from extrair_anexos_dia import anexos_do_paciente
from extrator_arquivos import _get_relatorio_analitico, _login_playwright
from extrator_odontoprev import normaliza_nome
from extrator_pacientes_analitico import BASE_URL as BASE, get_credentials
from solicitacao_utils import canon_exames

CONVENIOS_HAPVIDA = [
    "HAPVIDA - CENTRO", "HAPVIDA - ITAIGARA", "HAPVIDA - TANCREDO",
    "HAPVIDA - LAURO DE FREITAS", "HAPVIDA - PERIPERI", "HAPVIDA - CAMAÇARI",
]

# levantamento radiográfico = série de periapicais/interproximais; não tem canônico
_CANON_EXTRA = {"81000294": {"periapical", "interproximal"}}


def exames_canon(codigos) -> set:
    """Códigos TUSS da guia -> exames canônicos do filtro de particular."""
    out = set()
    for c in codigos or []:
        out |= _CANON_EXTRA.get(str(c)) or canon_exames(hd.CODIGOS.get(str(c), ""))
    return out


def resultado_da_busca(item: dict, codigos) -> dict:
    """Saída do _baixa_um -> dicionário da decisão. Pura: testável sem PRORADIS.
    ERRO (falha nossa, ex.: folhas incompletas) sobe como exceção -> 'erro' + retry."""
    st = item.get("status")
    if st == "AMBIGUO":
        return {"encontrado": True, "ambiguo": True, "nome": item.get("nome"),
                "dias_com_exame": item.get("dias_com_exame") or []}
    if st == "SEM_MATCH":
        return {"encontrado": False}
    if st == "ERRO":
        raise RuntimeError(item.get("erro") or "falha técnica no download do PRORADIS")

    pasta = item.get("_pasta")
    arquivos, excluidos, exames_fora = _filtrar_arquivos_da_gto(
        pasta, {"gto_exames_desta": sorted(exames_canon(codigos))},
        item.get("extras_acc"), item.get("convenio_acc"))
    base = lambda p: os.path.basename(p).upper()
    return {"encontrado": True, "ambiguo": False, "nome": item.get("nome"),
            # nascimento do PRORADIS não vem no download; o desempate de homônimo
            # pelo nascimento do Hapvida já aconteceu no caminho do RedeUna
            "nascimento": "",
            "entregaveis": [p for p in arquivos if base(p).startswith("ENTREGA_")],
            "laudos": [p for p in arquivos if base(p).startswith("LAUDO_")],
            "misto": bool(excluidos), "exames_fora": exames_fora,
            "data_exame_real": item.get("data_exame_real"),
            "pedido": None, "pedido_candidatos": []}


def _id_anexo(it) -> int:
    try:
        return int(re.sub(r"\D", "", str(it.get("id", ""))) or 0)
    except ValueError:
        return 0


class FonteProradis:
    """Uso: with FonteProradis() as fonte: fonte.buscar(nome, nascimento, dia, codigos, gto)."""

    def __init__(self, log=None, convenios=None):
        self.log = log or (lambda m: print(m, flush=True))
        self.convenios = list(convenios or CONVENIOS_HAPVIDA)
        self._pw = None
        self.browser = self.ctx = self.page = None
        self.tmp = tempfile.mkdtemp(prefix="_hap_pr_")
        self._by_norm = {}          # dia -> índice do analítico (1 consulta por dia)
        self._gem = None

    def __enter__(self):
        email, senha = get_credentials()
        self._pw = sync_playwright().start()
        self.browser, self.ctx, self.page = _login_playwright(self._pw, email, senha)
        self.ctx.set_default_timeout(45000)
        self.ctx.set_default_navigation_timeout(60000)
        return self

    def __exit__(self, *exc):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _analitico(self, dia: str) -> dict:
        if dia not in self._by_norm:
            df = _get_relatorio_analitico(self.page, self.convenios, [], dia)
            self._by_norm[dia] = _build_by_norm(df)
            self.log(f"[hapvida/proradis] analítico {dia}: "
                     f"{sum(len(v) for v in self._by_norm[dia].values())} pacientes HAPVIDA")
        return self._by_norm[dia]

    def buscar(self, nome: str, nascimento: str, dia: str, codigos: list, gto: str = "") -> dict:
        g = {"gto": gto, "nome": nome, "nome_norm": normaliza_nome(nome),
             "nascimento": nascimento or ""}
        item = _baixa_um(self.page, self.ctx, self._analitico(dia), g, self.tmp, dia)
        res = resultado_da_busca(item, codigos)
        precisa = any(str(c) in hd.EXIGE_PEDIDO for c in codigos or [])
        if (precisa and res.get("encontrado") and not res.get("ambiguo")
                and not res.get("misto") and res.get("entregaveis")):
            res["pedido_candidatos"] = self._anexos_prontuario(item, nascimento)
            self._pedido(res, nome, codigos, dia, item["_pasta"])
        return res

    def _gemini(self):
        if self._gem is None:
            key = os.environ.get("GEMINI_API_KEY")
            if not key:
                return None
            from google import genai
            self._gem = genai.Client(api_key=key)
        return self._gem

    def _pedido(self, res: dict, nome: str, codigos, dia: str, pasta: str) -> None:
        gem = self._gemini()
        if gem is None:
            res["pedido_motivo"] = "Leitura indisponível: GEMINI_API_KEY não configurada, o pedido do dentista não foi lido."
            res["pedido_responsavel"] = "Nós"
            return
        # ERRO do Gemini (crédito/cota/rede) sobe: o fluxo marca 'erro' (falha nossa)
        p = hpe.escolher_pedido(gem, res["pedido_candidatos"], res.get("nome") or nome,
                                codigos, res.get("data_exame_real") or dia,
                                os.path.join(pasta, "_pedido"))
        res["pedido"] = p.arquivos or None
        res["pedido_data"] = p.data
        if p.motivo:
            res["pedido_motivo"], res["pedido_responsavel"] = p.motivo, p.responsavel

    def _anexos_prontuario(self, item: dict, nascimento: str) -> list:
        pac, candidatos = item.get("_pac") or {}, []
        try:
            itens = anexos_do_paciente(self.page, pac.get("nome"), pac.get("cod_pac"), nascimento)
            sess = requests.Session()
            sess.cookies.update({c["name"]: c["value"] for c in self.ctx.cookies()})
            sess.headers.update({"User-Agent": "Mozilla/5.0", "Referer": f"{BASE}/patients"})
            dest = os.path.join(item["_pasta"], "_prontuario")
            os.makedirs(dest, exist_ok=True)
            # do MAIS RECENTE para o mais antigo (id decrescente), como a esteira:
            # "o pedido mais recente vence" depende dessa ordem
            itens = sorted(itens, key=_id_anexo, reverse=True)[:30]
            for n, it in enumerate(itens):
                nome_arq = os.path.basename(it.get("filename") or f"anexo_{it.get('id')}")
                p = os.path.join(dest, f"{n:02d}_{nome_arq}")
                with open(p, "wb") as f:
                    f.write(sess.get(it["url"], timeout=60).content)
                candidatos.append(p)
        except Exception as e:
            self.log(f"[hapvida/proradis] anexos do prontuário de {pac.get('nome')}: {str(e)[:120]}")
        return candidatos
