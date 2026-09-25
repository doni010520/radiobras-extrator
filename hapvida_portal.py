"""
hapvida_portal.py — Leitura das telas do Portal do Dentista Hapvida (sessão de extrator_hapvida).

Parsers puros (HTML -> dados) + a classe PortalHapvida que o hapvida_fluxo usa.
Todas as leituras são GET e não alteram nada no portal.

Telas (mapeadas em 13/09/2026, detalhes em _hap/REGRAS_HAPVIDA.md):
  lista do dia    WebDentalPrevencao.pr_Lista_Proced_Executados_N
  paciente        WebDentalAtendimento.pr_situacao_usuario
  anexos          webNewDentalPrestador.pr_imagens_anexadas
  form de upload  WebDentalAtendimento.pr_anexar_imagens
"""
from __future__ import annotations

import html as _html
import re
from datetime import datetime, timedelta

import extrator_hapvida as hv


def _celulas(tr: str) -> list[str]:
    return [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)]


def _texto(h: str) -> str:
    h = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S | re.I)
    t = _html.unescape(re.sub(r"<[^>]+>", "\n", h))
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", t))


# ── Parsers ──────────────────────────────────────────────────────────────────
def parse_executados(h: str) -> list[dict]:
    """Tabela de Procedimentos Executados. Colunas: PROCESSO | ESPECIALIDADE | COD.USUÁRIO |
    NOME | GUIA | CODIGO | NOME PROCEDIMENTO | DENTE | DT ATENDIMENTO | STATUS | VALOR | FRANQUIA."""
    linhas = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S | re.I):
        c = _celulas(tr)
        if len(c) >= 12 and re.fullmatch(r"8\d{7}", c[5] or "") and re.fullmatch(r"\d+", c[4] or ""):
            linhas.append({"processo": c[0], "especialidade": c[1], "usuario": c[2], "nome": c[3],
                           "guia": c[4], "codigo": c[5], "procedimento": c[6], "dente": c[7],
                           "dt_atendimento": c[8], "status": c[9], "valor": c[10]})
    return linhas


def parse_paciente(h: str) -> dict:
    t = _texto(h)

    def campo(rotulo):
        m = re.search(rotulo + r":\s*\n\s*([^\n]+)", t, re.I)
        return m.group(1).strip() if m else ""
    return {"carteira": campo("Carteira"), "nome": campo("Paciente"),
            "nascimento": campo("Data Nascimento"),
            "autorizado": "ATENDIMENTO AUTORIZADO" in t.upper()}


def parse_anexos(h: str) -> list[dict]:
    """Linhas do Banco de Imagens: uma por (imagem, item da guia)."""
    return [{"img_id": m.group(1), "guia": m.group(2), "arquivo": m.group(3)}
            for m in re.finditer(r'data-img-id="(\d+)"\s+data-nu-guia="(\d+)"\s+data-img-name="([^"]+)"', h)]


def parse_form_upload(h: str) -> dict:
    def hidden(form_nome):
        m = re.search(r'<form[^>]*name="%s".*?</form>' % form_nome, h, re.S | re.I)
        bloco = m.group(0) if m else ""
        acao = re.search(r'action="([^"]*)"', bloco, re.I)
        campos = {}
        for i in re.finditer(r"<input[^>]*>", bloco, re.I):
            a = i.group(0)
            if re.search(r'type="(checkbox|file)"', a, re.I):
                continue
            n = re.search(r'name="([^"]+)"', a, re.I)
            v = re.search(r'value="([^"]*)"', a, re.I)
            if n:
                campos[n.group(1)] = v.group(1) if v else ""
        return (acao.group(1) if acao else ""), campos
    acao_img, campos_img = hidden("form_img")
    acao_proc, campos_proc = hidden("form_proced")
    itens = [int(x) for x in re.findall(r'<input[^>]*type="checkbox"[^>]*name="pnu_item"[^>]*value="(\d+)"', h, re.I)
             or re.findall(r'<input[^>]*value="(\d+)"[^>]*name="pnu_item"', h, re.I)]
    return {"acao_img": acao_img, "campos_img": campos_img,
            "acao_proced": acao_proc, "campos_proced": campos_proc, "itens": itens}


# ── Adaptador usado pelo fluxo ───────────────────────────────────────────────
class PortalHapvida:
    """Leituras do portal numa unidade. `anexar` fica em hapvida_upload (trava própria)."""

    def __init__(self, unidade: str, sessao=None):
        self.unidade = unidade
        self.s = sessao or hv.login_hapvida(unidade)
        self._cache_anexos: dict = {}

    def _get(self, procedure, **params):
        h = self.s.get(procedure, **params)
        if re.search(r"SESS.O EXPIRADA OU INEXISTENTE", h, re.I):
            self.s = hv.login_hapvida(self.unidade)
            self._cache_anexos.clear()
            h = self.s.get(procedure, **params)
        return h

    def listar_executados(self, dia: str) -> list[dict]:
        return parse_executados(self._get(
            "WebDentalPrevencao.pr_Lista_Proced_Executados_N", pDesde=dia, pAte=dia,
            pCdEspecialidade="-1", pCpfPrestadorFisico="-1", pCdUsuario="", pCdProcedimento=""))

    def paciente(self, usuario: str) -> dict:
        return parse_paciente(self._get(
            "WebDentalAtendimento.pr_situacao_usuario", pCdUsuario=usuario, pFl_Checa_Usuario="S",
            pFl_Dados_Tratamento="S", pFl_Opcao_Menu="", pFl_Corpo="0"))

    def anexos(self, usuario: str, dia: str, forcar: bool = False) -> list[dict]:
        if forcar or usuario not in self._cache_anexos:
            d = datetime.strptime(dia, "%d/%m/%Y")
            self._cache_anexos[usuario] = parse_anexos(self._get(
                "webNewDentalPrestador.pr_imagens_anexadas", pCd_Usuario=usuario,
                pCd_prestador=self.s.cd_pessoa,
                pDesde=(d - timedelta(days=60)).strftime("%d/%m/%Y"),
                pAte=datetime.now().strftime("%d/%m/%Y")))
        return self._cache_anexos[usuario]

    def guia_tem_imagem(self, usuario: str, guia: str, dia: str) -> bool:
        return any(a["guia"] == str(guia) for a in self.anexos(usuario, dia))

    def form_upload(self, usuario: str, guia: str) -> dict:
        return parse_form_upload(self._get(
            "WebDentalAtendimento.pr_anexar_imagens", pCd_Usuario=usuario,
            pCdPessoa=self.s.cd_pessoa, pGuia=guia, pmovel="N", pfl_close_on_exit="true"))

    def anexar(self, usuario, guia, arquivos, itens):
        import hapvida_upload
        hapvida_upload.anexar(self, usuario, guia, arquivos, itens)
        self._cache_anexos.pop(usuario, None)   # a confirmação relê do portal
