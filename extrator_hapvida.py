"""
Integração com o Portal do Dentista Credenciado HAPVIDA +Odonto.

Portal Oracle PL/SQL (podontow). Não há SPA nem captcha ativo: o login é um POST
que devolve JSON, e a sessão viaja na QUERY STRING (pIdSessao/pNoCache/pCDPessoa)
junto com cookies de balanceador. Por isso usamos requests puro (sem navegador).

Credenciais por unidade, no .env / ambiente:
  HAPVIDA_CONTAS=centro,tancredo,itaigara
  HAPVIDA_<APELIDO>_USER=<cnpj>      HAPVIDA_<APELIDO>_PASSWORD=<senha>

Módulo ISOLADO do OdontoPrev: não importa nem altera nada do fluxo RedeUna.
"""

import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlparse, parse_qs

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE = "https://www.hapvida.com.br/pls/podontow/"
LOGIN_PAGE = BASE + "webNewDentalPrestador.pr_login"
LOGIN_POST = BASE + "webNewDentalPrestador.pr_validacao_login"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
ENCODING = "latin-1"          # o portal responde ISO-8859-1


class HapvidaLoginError(RuntimeError):
    """Falha de login. `transitorio` separa rede/servidor de credencial errada."""
    def __init__(self, conta: str, motivo: str, transitorio: bool):
        super().__init__(f"[hapvida:{conta}] {motivo}")
        self.conta = conta
        self.motivo = motivo
        self.transitorio = transitorio


# ── Credenciais ──────────────────────────────────────────────────────────────
def listar_contas_hapvida() -> list[str]:
    """Apelidos das unidades configuradas em HAPVIDA_CONTAS (minúsculos, sem vazios)."""
    bruto = os.environ.get("HAPVIDA_CONTAS", "")
    return [c.strip().lower() for c in bruto.split(",") if c.strip()]


def get_credentials_hapvida(conta: str) -> tuple[str, str]:
    chave = conta.strip().upper()
    user = (os.environ.get(f"HAPVIDA_{chave}_USER") or "").strip()
    pwd = (os.environ.get(f"HAPVIDA_{chave}_PASSWORD") or "").strip()
    if not user or not pwd:
        raise RuntimeError(
            f"Credenciais do Hapvida ausentes para '{conta}'. Defina "
            f"HAPVIDA_{chave}_USER e HAPVIDA_{chave}_PASSWORD no ambiente (ou .env).")
    return re.sub(r"\D", "", user), pwd


# ── Sessão ───────────────────────────────────────────────────────────────────
@dataclass
class HapvidaSessao:
    conta: str
    http: requests.Session
    id_sessao: str
    no_cache: str
    cd_pessoa: str
    prestador: str = ""
    extras: dict = field(default_factory=dict)

    def params(self) -> dict:
        return {"pIdSessao": self.id_sessao, "pNoCache": self.no_cache,
                "pCDPessoa": self.cd_pessoa}

    def url(self, procedure: str, **extra) -> str:
        """URL de uma tela interna já com a sessão. Ex.: s.url('webNewDentalPrestador.pr_Recurso_Glosa')."""
        return BASE + procedure + "?" + urlencode({**self.params(), **extra})

    def get(self, procedure: str, **extra) -> str:
        r = self.http.get(self.url(procedure, **extra), timeout=60)
        r.raise_for_status()
        return r.content.decode(ENCODING, "replace")


def interpretar_resposta_login(corpo: str) -> dict:
    """Lê o JSON do pr_validacao_login. Retorna dict com ok, url, msg e os ids de sessão."""
    try:
        ret = json.loads(corpo)
    except (ValueError, TypeError):
        return {"ok": False, "msg": "resposta de login não é JSON", "url": ""}
    url = ret.get("url") or ""
    q = {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}
    ok = (str(ret.get("status")) == "0" and "pr_prestador" in url.lower()
          and bool(q.get("pidsessao")))
    return {"ok": ok, "url": url, "msg": (ret.get("msg") or "").strip(),
            "id_sessao": q.get("pidsessao", ""), "no_cache": q.get("pnocache", ""),
            "cd_pessoa": q.get("pcdpessoa", "")}


def pagina_logada(html: str) -> str | None:
    """Confirma a home do prestador. Devolve o nome do prestador, ou None."""
    if re.search(r"SESS.O EXPIRADA OU INEXISTENTE", html, re.I):
        return None
    m = re.search(r"Bem Vindo\(a\)\s*(.+?)\s*ao seu portal", html, re.I | re.S)
    return re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else None


def login_hapvida(conta: str, tentativas: int = 3) -> HapvidaSessao:
    """Loga uma unidade e devolve a sessão confirmada. Lança HapvidaLoginError."""
    user, pwd = get_credentials_hapvida(conta)
    ultimo = None
    for _ in range(tentativas):
        http = requests.Session()
        http.headers["User-Agent"] = UA
        try:
            http.get(LOGIN_PAGE, timeout=30)
            r = http.post(LOGIN_POST, timeout=30,
                          data={"pIdSessao": "", "pNoCache": "", "pCpfCnpj": user,
                                "pSenha": pwd, "pOrgAmb": "", "pTokenCaptcha": ""},
                          headers={"X-Requested-With": "XMLHttpRequest",
                                   "Referer": LOGIN_PAGE})
            r.raise_for_status()
        except requests.RequestException as e:
            ultimo = HapvidaLoginError(conta, f"falha de rede: {e}", transitorio=True)
            continue

        info = interpretar_resposta_login(r.content.decode(ENCODING, "replace"))
        if not info["ok"]:
            # Portal respondeu e recusou: credencial/cadastro — não adianta repetir.
            raise HapvidaLoginError(conta, info["msg"] or "login recusado pelo portal",
                                    transitorio=False)

        sessao = HapvidaSessao(conta=conta, http=http, id_sessao=info["id_sessao"],
                               no_cache=info["no_cache"], cd_pessoa=info["cd_pessoa"])
        try:
            home = http.get(BASE + info["url"], timeout=60).content.decode(ENCODING, "replace")
        except requests.RequestException as e:
            ultimo = HapvidaLoginError(conta, f"falha ao abrir a home: {e}", transitorio=True)
            continue
        nome = pagina_logada(home)
        if not nome:
            ultimo = HapvidaLoginError(conta, "login aceito mas a home não confirmou a sessão",
                                       transitorio=True)
            continue
        sessao.prestador = nome
        return sessao
    raise ultimo


if __name__ == "__main__":
    # Smoke test: python extrator_hapvida.py  -> loga todas as unidades (só leitura).
    contas = listar_contas_hapvida()
    if not contas:
        raise SystemExit("HAPVIDA_CONTAS vazio no ambiente.")
    falhas = 0
    for c in contas:
        try:
            s = login_hapvida(c)
            print(f"[OK]    {c:10s} cdPessoa={s.cd_pessoa:10s} {s.prestador}")
        except (HapvidaLoginError, RuntimeError) as e:
            falhas += 1
            tipo = "transitório" if getattr(e, "transitorio", False) else "credencial/config"
            print(f"[FALHA] {c:10s} ({tipo}) {e}")
    raise SystemExit(1 if falhas else 0)
