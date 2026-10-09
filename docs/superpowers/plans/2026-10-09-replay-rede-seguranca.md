# Rede de segurança por replay — Plano de Implementação (Fase 1 de 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gravar rodadas DRY reais (tudo que vem de fora: OdontoPrev, PRORADIS, Gemini, banco, relógio) em "cassetes" e repeti-las offline de forma determinística, produzindo um veredito normalizado por guia, para provar que as reorganizações das Fases 2–5 não mudam nenhuma decisão de faturamento.

**Architecture:** Um módulo `replay_harness.py` substitui, dentro do módulo `esteira`, as funções de fronteira ("costuras") por gravadores (modo GRAVAR, chamam a função real e guardam o resultado) ou tocadores (modo TOCAR, devolvem o gravado e falham alto se faltar). O ponto de entrada continua `esteira.rodar_esteira(dry_run=True)`, então a rede vale para qualquer reorganização interna. Dois CLIs (`replay_gravar.py`, `replay_comparar.py`) gravam cassetes e comparam o veredito atual com uma linha de base.

**Tech Stack:** Python 3.13, pytest, Playwright (só na gravação), requests, google-genai (só na gravação), pandas (DataFrame do analítico).

## Contexto do roadmap (para quem lê fora de ordem)

1. **Fase 1 (este plano):** rede de segurança por replay.
2. Fase 2: remover legado (tabelas `runs`/`run_itens`, `fechar_dia.py` e fluxo `_jobs` antigo), repositório e `.env` únicos.
3. Fase 3: porta única "achar o paciente" (`identidade.py`).
4. Fase 4: quebrar `rodar_esteira` (1.218 linhas) nas etapas descoberta/download/decisão/anexação.
5. Fase 5: porta única "esta guia pode faturar?" (`regras.py`), incluindo as decisões pendentes do dono (trava do dentista; nome manuscrito).

Cada fase tem plano próprio, escrito quando a anterior termina.

## Global Constraints

- Repositório: worktree `C:\Users\adoni\rb-prod`, branch `fix/modelo-rotulo-portal` (= `origin/master` em produção). Nada aqui muda comportamento de produção.
- **LGPD:** cassetes contêm documento médico e nome de paciente. Ficam FORA do git, em `C:\Users\adoni\rb-replay\cassetes\` (variável `RB_REPLAY_DIR` sobrepõe). Nunca commitar cassete nem linha de base com motivo/nome.
- A linha de base (`baseline.json`) fica junto dos cassetes, também fora do git.
- Ambiente Windows + Git Bash: `python3` é o Python do Windows; caminhos dentro de Python em formato `C:\...`; sempre `export PYTHONIOENCODING=utf-8`.
- Gravação roda nesta máquina, SEM proxy (`unset ODONTO_PROXY_URL`), com `m_download=1` (vários Chromium em paralelo travam a máquina). Sempre `dry_run=True`: a gravação NUNCA anexa.
- Suíte atual: 918 testes passando (`python3 -m pytest -q -p no:warnings`). Continua passando ao fim de cada tarefa.
- Commits terminam com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Texto para o dono em português, sem travessão.

## File Structure

- Create `replay_harness.py` — Cassete (armazenamento), chaves estáveis, falsos do Playwright, gravadores/tocadores de cada costura, `instalar(modo, cassete)` (context manager que aplica e desfaz os patches), `veredito(resumo)` (normalização).
- Create `replay_gravar.py` — CLI: grava cassetes de (conta, dia) rodando `rodar_esteira(dry_run=True)` real.
- Create `replay_comparar.py` — CLI: toca todos os cassetes, gera veredito, salva/compara com `baseline.json`, imprime diferenças.
- Create `test_replay_harness.py` — testes unitários do harness (sem rede).
- Create `test_replay_cassetes.py` — teste que toca os cassetes reais e compara com a linha de base; `skip` se a pasta não existir (CI/servidor).

### Costuras (funções do namespace `esteira` substituídas)

| Costura | Chave | Valor gravado |
|---|---|---|
| `sync_playwright` | — | falso (sem IO) |
| `login_odonto(pw, user, pwd)` | — | `(br, ctx, pg)` falsos |
| `_login_playwright(pw, email, pwd)` | — | `(br, ctx, pg)` falsos |
| `abrir_consultar_gtos`, `consultar_periodo` | — | no-op |
| `abrir_gto(pg, gto, _refrescar=None)` | — | página falsa |
| `listar_gtos(pg)` | `"listar_gtos"` | lista JSON |
| `_get_relatorio_analitico(pg, conv, seg, data)` | `data` | DataFrame (`to_json(orient="split")`) |
| `_baixa_um(pg, ctx, by_norm, g, tmp, data)` | `g["gto"]` | dict + arquivos da pasta |
| `anexos_do_paciente(pg, nome, cod, nascimento=None)` | `nome|cod|nascimento` | lista JSON |
| `requests.Session` (classe) | `METODO url params` | status, texto, bytes |
| `google.genai.Client` | sha256(modelo + conteúdo) | `.text` |
| `_carregar_confirmados()` | `"confirmados"` | lista |
| `datetime` (só `now()`) | — | instante da gravação |
| `get_credentials()` (PRORADIS) | — | `("replay", "replay")` só no modo tocar |

---

### Task 1: Cassete e chaves estáveis

**Files:**
- Create: `replay_harness.py`
- Test: `test_replay_harness.py`

**Interfaces:**
- Produces: `class Cassete(pasta: str)` com `gravar(costura: str, chave: str, valor) -> None`, `tocar(costura, chave)` (levanta `ReplayFaltando`), `gravar_blob(dados: bytes) -> str` (devolve sha), `ler_blob(sha) -> bytes`, `salvar()`, `meta: dict`; `class ReplayFaltando(Exception)`; `chave_gemini(modelo, conteudo) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# test_replay_harness.py
import pytest
import replay_harness as rh


def test_cassete_grava_e_toca_valor_e_blob(tmp_path):
    c = rh.Cassete(str(tmp_path / "c1"))
    sha = c.gravar_blob(b"PDF-bytes")
    c.gravar("baixa_um", "197100679", {"status": "BAIXADO", "blob": sha})
    c.meta["dia"] = "08/09/2026"
    c.salvar()
    c2 = rh.Cassete(str(tmp_path / "c1"))
    assert c2.tocar("baixa_um", "197100679")["status"] == "BAIXADO"
    assert c2.ler_blob(sha) == b"PDF-bytes"
    assert c2.meta["dia"] == "08/09/2026"


def test_tocar_chave_ausente_falha_alto(tmp_path):
    c = rh.Cassete(str(tmp_path / "c2"))
    with pytest.raises(rh.ReplayFaltando, match="baixa_um"):
        c.tocar("baixa_um", "999")


def test_chave_gemini_estavel_e_sensivel_ao_conteudo():
    class _Part:
        def __init__(self, text=None, data=None, mime=None):
            self.text = text
            self.inline_data = type("D", (), {"data": data, "mime_type": mime})() if data else None
    a = rh.chave_gemini("gemini-2.5-flash", ["leia", _Part(data=b"\x01\x02", mime="image/png")])
    b = rh.chave_gemini("gemini-2.5-flash", ["leia", _Part(data=b"\x01\x02", mime="image/png")])
    c = rh.chave_gemini("gemini-2.5-flash", ["leia", _Part(data=b"\x01\x03", mime="image/png")])
    assert a == b and a != c
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/adoni/rb-prod && python3 -m pytest -q -p no:warnings test_replay_harness.py`
Expected: FAIL (`ModuleNotFoundError: replay_harness`)

- [ ] **Step 3: Write minimal implementation**

```python
# replay_harness.py
"""Rede de seguranca por replay (Fase 1 da reorganizacao, 09/10/2026).

Grava uma rodada DRY real (tudo que vem de fora) num CASSETE e a repete offline,
de forma deterministica, para provar que uma reorganizacao nao muda decisao de
faturamento. Cassete tem documento medico: fica FORA do git (RB_REPLAY_DIR)."""
import hashlib
import json
import os
import threading

PASTA_PADRAO = os.environ.get("RB_REPLAY_DIR", r"C:\Users\adoni\rb-replay\cassetes")


class ReplayFaltando(Exception):
    """O codigo pediu algo que a gravacao nao tem: a reorganizacao mudou uma
    chamada externa (ou o cassete esta incompleto). Nunca inventar resposta."""


class Cassete:
    def __init__(self, pasta: str):
        self.pasta = pasta
        self._lock = threading.Lock()
        self._arq = os.path.join(pasta, "chamadas.json")
        self._dados, self.meta = {}, {}
        if os.path.exists(self._arq):
            with open(self._arq, encoding="utf-8") as f:
                j = json.load(f)
            self._dados, self.meta = j.get("chamadas", {}), j.get("meta", {})

    def gravar(self, costura: str, chave: str, valor) -> None:
        with self._lock:
            self._dados.setdefault(costura, {})[str(chave)] = valor

    def tocar(self, costura: str, chave: str):
        try:
            return self._dados[costura][str(chave)]
        except KeyError:
            raise ReplayFaltando(f"{costura}: chave nao gravada {str(chave)[:160]!r}")

    def gravar_blob(self, dados: bytes) -> str:
        sha = hashlib.sha256(dados or b"").hexdigest()
        d = os.path.join(self.pasta, "blobs")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, sha)
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(dados or b"")
        return sha

    def ler_blob(self, sha: str) -> bytes:
        with open(os.path.join(self.pasta, "blobs", sha), "rb") as f:
            return f.read()

    def salvar(self) -> None:
        os.makedirs(self.pasta, exist_ok=True)
        with self._lock, open(self._arq, "w", encoding="utf-8") as f:
            json.dump({"meta": self.meta, "chamadas": self._dados}, f,
                      ensure_ascii=False, indent=1, default=str)


def _forma(x, h):
    """Alimenta o hash com a FORMA do conteudo enviado ao Gemini: texto como texto,
    bytes pelo sha. Objetos do SDK (Part) lidos por atributo, sem importar o SDK."""
    if x is None:
        h.update(b"N")
    elif isinstance(x, str):
        h.update(b"S" + x.encode("utf-8"))
    elif isinstance(x, (bytes, bytearray)):
        h.update(b"B" + hashlib.sha256(bytes(x)).digest())
    elif isinstance(x, (list, tuple)):
        h.update(b"L")
        for i in x:
            _forma(i, h)
    elif isinstance(x, dict):
        for k in sorted(x):
            h.update(b"K" + str(k).encode())
            _forma(x[k], h)
    else:
        for at in ("text", "inline_data", "data", "mime_type", "parts", "file_data"):
            if hasattr(x, at):
                h.update(b"A" + at.encode())
                _forma(getattr(x, at), h)


def chave_gemini(modelo, conteudo) -> str:
    h = hashlib.sha256(str(modelo).encode())
    _forma(conteudo, h)
    return h.hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/adoni/rb-prod && python3 -m pytest -q -p no:warnings test_replay_harness.py`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add replay_harness.py test_replay_harness.py
git commit -m "replay: cassete e chaves estaveis (Fase 1 da reorganizacao)"
```

---

### Task 2: Falsos do Playwright, Gemini e requests

**Files:**
- Modify: `replay_harness.py`
- Test: `test_replay_harness.py`

**Interfaces:**
- Consumes: `Cassete`, `chave_gemini`, `ReplayFaltando` (Task 1).
- Produces: `PaginaFalsa`, `ContextoFalso`, `NavegadorFalso`, `PlaywrightFalso` (sync_playwright falso: `with PlaywrightFalso() as pw` e `PlaywrightFalso().start()`); `gemini_cliente(modo, cassete, real_cls)` devolve uma classe-fábrica compatível com `genai.Client(api_key=...)`; `sessao_classe(modo, cassete, real_cls)` devolve classe compatível com `requests.Session()`.

- [ ] **Step 1: Write the failing test**

```python
def test_contexto_falso_entrega_bearer_para_quem_escuta_request():
    ctx = rh.ContextoFalso()
    vistos = []
    ctx.on("request", lambda req: vistos.append(req.headers.get("authorization")))
    assert vistos == ["Bearer REPLAY"]
    assert ctx.cookies() == [] and ctx.storage_state() == {}


def test_playwright_falso_nos_dois_jeitos_de_uso():
    with rh.PlaywrightFalso() as pw:
        br = pw.chromium.launch(headless=True)
        pg = br.new_context().new_page()
        pg.goto("x"); pg.wait_for_timeout(5)
    p2 = rh.PlaywrightFalso().start(); p2.stop()


def test_gemini_grava_e_toca(tmp_path):
    c = rh.Cassete(str(tmp_path / "g"))

    class _RealResp:
        text = '{"ok": true}'

    class _RealModels:
        def generate_content(self, model, contents, config=None):
            return _RealResp()

    class _RealClient:
        def __init__(self, api_key=None):
            self.models = _RealModels()

    Grav = rh.gemini_cliente("gravar", c, _RealClient)
    assert Grav(api_key="k").models.generate_content(model="m", contents=["oi"]).text == '{"ok": true}'
    Toca = rh.gemini_cliente("tocar", c, None)
    assert Toca(api_key="k").models.generate_content(model="m", contents=["oi"]).text == '{"ok": true}'
    with pytest.raises(rh.ReplayFaltando):
        Toca(api_key="k").models.generate_content(model="m", contents=["outro"])


def test_requests_grava_e_toca_bytes_e_status(tmp_path):
    c = rh.Cassete(str(tmp_path / "r"))

    class _R:
        status_code, text, content, headers = 200, '[{"a":1}]', b'[{"a":1}]', {"Content-Type": "application/json"}
        def json(self):
            return [{"a": 1}]

    class _RealSess:
        def __init__(self):
            self.headers, self.cookies = {}, type("J", (), {"update": lambda s, x: None})()
        def get(self, url, **kw):
            return _R()

    G = rh.sessao_classe("gravar", c, _RealSess)
    assert G().get("https://x/v1/gto/imagens?numeroFicha=1", timeout=20).json() == [{"a": 1}]
    T = rh.sessao_classe("tocar", c, None)
    r = T().get("https://x/v1/gto/imagens?numeroFicha=1", timeout=5)
    assert r.status_code == 200 and r.json() == [{"a": 1}] and r.content == b'[{"a":1}]'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/adoni/rb-prod && python3 -m pytest -q -p no:warnings test_replay_harness.py`
Expected: FAIL (`AttributeError: ... ContextoFalso`)

- [ ] **Step 3: Write minimal implementation** (anexar ao fim de `replay_harness.py`)

```python
# ── falsos do Playwright: aceitam qualquer chamada e nao fazem IO ─────────────
class _Nada:
    """Qualquer metodo desconhecido vira no-op que devolve None."""
    def __getattr__(self, nome):
        return lambda *a, **k: None


class PaginaFalsa(_Nada):
    url = "about:blank"
    keyboard = _Nada()

    def evaluate(self, *a, **k):
        return None

    def query_selector(self, *a, **k):
        return None

    def query_selector_all(self, *a, **k):
        return []


class _ReqFalso:
    url = "https://credenciado.odontoprev.com.br/replay"
    headers = {"authorization": "Bearer REPLAY"}


class ContextoFalso(_Nada):
    def on(self, evento, fn):
        if evento == "request":      # entrega o Bearer a quem escuta (descoberta)
            fn(_ReqFalso())

    def new_page(self):
        return PaginaFalsa()

    def cookies(self):
        return []

    def storage_state(self, *a, **k):
        return {}


class NavegadorFalso(_Nada):
    def new_context(self, *a, **k):
        return ContextoFalso()


class _Chromium:
    def launch(self, *a, **k):
        return NavegadorFalso()


class PlaywrightFalso:
    chromium = _Chromium()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def start(self):
        return self

    def stop(self):
        pass


def trio_falso(*a, **k):
    return NavegadorFalso(), ContextoFalso(), PaginaFalsa()


# ── Gemini ──────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, text):
        self.text = text


def gemini_cliente(modo, cassete, real_cls):
    class _Models:
        def __init__(self, real):
            self._real = real

        def generate_content(self, model=None, contents=None, config=None, **kw):
            k = chave_gemini(model, contents)
            if modo == "tocar":
                return _Resp(cassete.tocar("gemini", k))
            r = self._real.generate_content(model=model, contents=contents, config=config, **kw)
            cassete.gravar("gemini", k, getattr(r, "text", None))
            return r

    class _Cliente:
        def __init__(self, *a, **k):
            real = real_cls(*a, **k).models if modo == "gravar" else None
            self.models = _Models(real)

    return _Cliente


# ── requests.Session ────────────────────────────────────────────────────────
class _RespHttp:
    def __init__(self, status, conteudo, headers):
        self.status_code = status
        self.content = conteudo
        self.headers = headers or {}
        self.text = conteudo.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


def _chave_http(metodo, url, kw):
    extra = {k: kw[k] for k in ("params", "data", "json") if kw.get(k) is not None}
    return f"{metodo} {url} " + json.dumps(extra, sort_keys=True, default=str)


def sessao_classe(modo, cassete, real_cls):
    class _Sessao:
        def __init__(self, *a, **k):
            self._real = real_cls(*a, **k) if modo == "gravar" else None
            self.headers = self._real.headers if self._real else {}
            self.cookies = self._real.cookies if self._real else _Nada()

        def _faz(self, metodo, url, **kw):
            k = _chave_http(metodo, url, kw)
            if modo == "tocar":
                v = cassete.tocar("http", k)
                return _RespHttp(v["status"], cassete.ler_blob(v["blob"]), v["headers"])
            r = getattr(self._real, metodo.lower())(url, **kw)
            cassete.gravar("http", k, {"status": r.status_code,
                                       "blob": cassete.gravar_blob(r.content),
                                       "headers": dict(r.headers)})
            return r

        def get(self, url, **kw):
            return self._faz("GET", url, **kw)

        def post(self, url, **kw):
            return self._faz("POST", url, **kw)

        def mount(self, *a, **k):
            if self._real:
                self._real.mount(*a, **k)

        def close(self):
            if self._real:
                self._real.close()

    return _Sessao
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/adoni/rb-prod && python3 -m pytest -q -p no:warnings test_replay_harness.py`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add replay_harness.py test_replay_harness.py
git commit -m "replay: falsos de Playwright, Gemini e requests"
```

---

### Task 3: Instalar as costuras no módulo `esteira` e o veredito normalizado

**Files:**
- Modify: `replay_harness.py`
- Test: `test_replay_harness.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `instalar(modo: str, cassete: Cassete)` (context manager; ao sair, restaura tudo e, se `modo == "gravar"`, salva o cassete); `veredito(resumo: dict) -> dict[str, dict]` mapeando gto → `{"categoria", "anexado", "arquivos", "motivo"}`.

- [ ] **Step 1: Write the failing test**

```python
import esteira


def test_instalar_troca_e_restaura_as_costuras(tmp_path):
    original = esteira._baixa_um
    c = rh.Cassete(str(tmp_path / "i"))
    c.gravar("baixa_um", "123", {"gto": "123", "status": "SEM_MATCH", "arquivos": []})
    with rh.instalar("tocar", c):
        assert esteira._baixa_um is not original
        r = esteira._baixa_um(None, None, {}, {"gto": "123"}, str(tmp_path), "01/10/2026")
        assert r["status"] == "SEM_MATCH"
    assert esteira._baixa_um is original


def test_baixa_um_recria_a_pasta_com_os_mesmos_bytes(tmp_path):
    c = rh.Cassete(str(tmp_path / "p"))
    sha = c.gravar_blob(b"LAUDO")
    c.gravar("baixa_um", "9", {"gto": "9", "status": "BAIXADO",
                               "_arquivos": {"LAUDO_PANORAMICA_1_OFICIAL.pdf": sha},
                               "_pasta": "X"})
    with rh.instalar("tocar", c):
        r = esteira._baixa_um(None, None, {}, {"gto": "9"}, str(tmp_path / "tmp"), "01/10/2026")
    import os
    assert open(os.path.join(r["_pasta"], "LAUDO_PANORAMICA_1_OFICIAL.pdf"), "rb").read() == b"LAUDO"


def test_relogio_congelado_no_instante_da_gravacao(tmp_path):
    c = rh.Cassete(str(tmp_path / "t"))
    c.meta["agora"] = "2026-10-09T10:00:00"
    with rh.instalar("tocar", c):
        assert esteira.datetime.now().isoformat().startswith("2026-10-09T10:00")


def test_veredito_normaliza_e_indexa_por_gto():
    resumo = {"decisoes": [
        {"gto": "2", "categoria": "auto", "anexado": "DRY",
         "arquivos_anexados": ["b.pdf", "a.jpg"], "gemini": {"motivo": "ok"}},
        {"gto": "1", "categoria": "sem_exame", "anexado": None, "gemini": {"motivo": "x"},
         "erro": None}]}
    v = rh.veredito(resumo)
    assert list(v) == ["1", "2"]
    assert v["2"] == {"categoria": "auto", "anexado": "DRY", "arquivos": ["a.jpg", "b.pdf"], "motivo": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/adoni/rb-prod && python3 -m pytest -q -p no:warnings test_replay_harness.py`
Expected: FAIL (`AttributeError: ... instalar`)

- [ ] **Step 3: Write minimal implementation** (anexar ao fim de `replay_harness.py`)

```python
import contextlib
from datetime import datetime as _dt_real


def _df_para_json(df):
    return df.to_json(orient="split", force_ascii=False)


def _json_para_df(s):
    import io
    import pandas as pd
    return pd.read_json(io.StringIO(s), orient="split", dtype=False)


@contextlib.contextmanager
def instalar(modo: str, cassete: Cassete):
    """Troca as costuras de `esteira` por gravadores (modo 'gravar') ou tocadores
    ('tocar'); restaura ao sair. A lista de costuras e a tabela do plano."""
    import esteira as E
    import google.genai as G
    import requests as R
    assert modo in ("gravar", "tocar")
    orig = {n: getattr(E, n) for n in (
        "sync_playwright", "login_odonto", "_login_playwright", "abrir_consultar_gtos",
        "consultar_periodo", "abrir_gto", "listar_gtos", "_get_relatorio_analitico",
        "_baixa_um", "anexos_do_paciente", "_carregar_confirmados", "datetime",
        "get_credentials")}
    orig_client, orig_sess = G.Client, E.requests.Session
    tocar = modo == "tocar"

    def _listar_gtos(pg):
        if tocar:
            return cassete.tocar("listar_gtos", "listar_gtos")
        v = orig["listar_gtos"](pg)
        cassete.gravar("listar_gtos", "listar_gtos", v)
        return v

    def _analitico(pg, conv, seg, data):
        if tocar:
            return _json_para_df(cassete.tocar("analitico", data))
        df = orig["_get_relatorio_analitico"](pg, conv, seg, data)
        cassete.gravar("analitico", data, _df_para_json(df))
        return df

    def _baixa(pg, ctx, by_norm, g, tmp, data):
        k = str(g.get("gto"))
        if tocar:
            v = dict(cassete.tocar("baixa_um", k))
            arqs = v.pop("_arquivos", None) or {}
            if "_pasta" in v or arqs:
                pasta = os.path.join(tmp, f"replay_{k}")
                os.makedirs(pasta, exist_ok=True)
                for nome, sha in arqs.items():
                    with open(os.path.join(pasta, nome), "wb") as f:
                        f.write(cassete.ler_blob(sha))
                v["_pasta"] = pasta
            return v
        r = orig["_baixa_um"](pg, ctx, by_norm, g, tmp, data)
        v = {kk: vv for kk, vv in r.items()}
        pasta = r.get("_pasta")
        if pasta and os.path.isdir(pasta):
            v["_arquivos"] = {}
            for nome in sorted(os.listdir(pasta)):
                with open(os.path.join(pasta, nome), "rb") as f:
                    v["_arquivos"][nome] = cassete.gravar_blob(f.read())
        cassete.gravar("baixa_um", k, json.loads(json.dumps(v, default=str)))
        return r

    def _anexos(pg, nome, cod, nascimento=None):
        k = f"{nome}|{cod}|{nascimento}"
        if tocar:
            v = cassete.tocar("anexos_do_paciente", k)
            if isinstance(v, dict) and v.get("_erro"):
                raise RuntimeError(v["_erro"])
            return v
        try:
            v = orig["anexos_do_paciente"](pg, nome, cod, nascimento)
        except Exception as e:
            cassete.gravar("anexos_do_paciente", k, {"_erro": str(e)})
            raise
        cassete.gravar("anexos_do_paciente", k, v)
        return v

    def _confirmados():
        if tocar:
            return set(cassete.tocar("confirmados", "confirmados"))
        v = orig["_carregar_confirmados"]()
        cassete.gravar("confirmados", "confirmados", sorted(v))
        return v

    agora = (_dt_real.fromisoformat(cassete.meta["agora"]) if tocar and cassete.meta.get("agora")
             else _dt_real.now())
    if not tocar:
        cassete.meta["agora"] = agora.isoformat()

    class _Relogio(_dt_real):
        @classmethod
        def now(cls, tz=None):
            return agora if tz is None else agora.astimezone(tz)

    novos = {"listar_gtos": _listar_gtos, "_get_relatorio_analitico": _analitico,
             "_baixa_um": _baixa, "anexos_do_paciente": _anexos,
             "_carregar_confirmados": _confirmados, "datetime": _Relogio}
    if tocar:
        novos.update({"sync_playwright": PlaywrightFalso, "login_odonto": trio_falso,
                      "_login_playwright": trio_falso,
                      "abrir_consultar_gtos": lambda *a, **k: None,
                      "consultar_periodo": lambda *a, **k: None,
                      "abrir_gto": lambda *a, **k: PaginaFalsa(),
                      "get_credentials": lambda *a, **k: ("replay", "replay")})
    try:
        for n, f in novos.items():
            setattr(E, n, f)
        G.Client = gemini_cliente(modo, cassete, orig_client)
        E.requests.Session = sessao_classe(modo, cassete, orig_sess)
        yield cassete
    finally:
        for n, f in orig.items():
            setattr(E, n, f)
        G.Client, E.requests.Session = orig_client, orig_sess
        if not tocar:
            cassete.salvar()


def veredito(resumo: dict) -> dict:
    """gto -> o que importa para faturamento, em forma comparavel."""
    out = {}
    for d in sorted((resumo or {}).get("decisoes") or [], key=lambda x: str(x.get("gto"))):
        g = str(d.get("gto"))
        motivo = ((d.get("gemini") or {}).get("motivo")) or d.get("erro") or ""
        out[g] = {"categoria": d.get("categoria"), "anexado": d.get("anexado"),
                  "arquivos": sorted(os.path.basename(str(a))
                                     for a in (d.get("arquivos_anexados") or [])),
                  "motivo": str(motivo)}
    return out
```

> Nota para o implementador: `esteira.requests` é o módulo `requests` importado em `esteira.py`; trocar `E.requests.Session` troca a classe para TODO o processo durante o `with`. Por isso o `finally` restaura sempre. Se `esteira` não importar `anexos_do_paciente`/`listar_gtos`/`abrir_gto` com esses nomes, ajuste a lista `orig` aos nomes reais (`grep -n "^from\|^import" esteira.py`) e registre a diferença no commit.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/adoni/rb-prod && python3 -m pytest -q -p no:warnings test_replay_harness.py && python3 -m pytest -q -p no:warnings`
Expected: 11 passed no arquivo; suíte inteira passando (929)

- [ ] **Step 5: Commit**

```bash
git add replay_harness.py test_replay_harness.py
git commit -m "replay: instala costuras em esteira e normaliza o veredito"
```

---

### Task 4: CLIs de gravar e comparar, e o teste dos cassetes reais

**Files:**
- Create: `replay_gravar.py`, `replay_comparar.py`, `test_replay_cassetes.py`

**Interfaces:**
- Consumes: `Cassete`, `instalar`, `veredito`, `PASTA_PADRAO`.
- Produces: `replay_comparar.tocar_todos(pasta=PASTA_PADRAO) -> dict[str, dict]` (nome do cassete → veredito) e `replay_comparar.diferencas(base, atual) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# test_replay_cassetes.py
"""Toca todos os cassetes gravados e compara com a linha de base. Sem a pasta
(servidor, CI), o teste e pulado: cassete tem documento medico e nao vai pro git."""
import json
import os

import pytest

import replay_comparar as rc
import replay_harness as rh

BASE = os.path.join(rh.PASTA_PADRAO, "baseline.json")


@pytest.mark.skipif(not os.path.exists(BASE), reason="sem cassetes nesta maquina")
def test_decisoes_iguais_a_linha_de_base():
    base = json.load(open(BASE, encoding="utf-8"))
    dif = rc.diferencas(base, rc.tocar_todos())
    assert not dif, "\n".join(dif[:40])


def test_diferencas_aponta_guia_que_mudou():
    base = {"c1": {"1": {"categoria": "auto"}}}
    atual = {"c1": {"1": {"categoria": "sem_exame"}}}
    assert rc.diferencas(base, atual) == ["c1 gto 1: categoria auto -> sem_exame"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/adoni/rb-prod && python3 -m pytest -q -p no:warnings test_replay_cassetes.py`
Expected: FAIL (`ModuleNotFoundError: replay_comparar`)

- [ ] **Step 3: Write minimal implementation**

```python
# replay_comparar.py
"""Toca os cassetes e compara o veredito com baseline.json.
Uso: python3 replay_comparar.py            -> compara e imprime diferencas
     python3 replay_comparar.py --baseline -> grava a linha de base atual"""
import json
import os
import sys
import tempfile

import replay_harness as rh


def tocar_um(pasta_cassete: str) -> dict:
    import esteira
    c = rh.Cassete(pasta_cassete)
    m = c.meta
    with rh.instalar("tocar", c):
        r = esteira.rodar_esteira(m["dia"], 1, 1, 5, log=lambda s: None,
                                  gemini_key="replay", review_dir=tempfile.mkdtemp(),
                                  k_attach=1, dry_run=True, conta=m["conta"],
                                  senha_portal="replay")
    return rh.veredito(r)


def tocar_todos(pasta: str = rh.PASTA_PADRAO) -> dict:
    out = {}
    for nome in sorted(os.listdir(pasta)):
        p = os.path.join(pasta, nome)
        if os.path.isfile(os.path.join(p, "chamadas.json")):
            out[nome] = tocar_um(p)
    return out


def diferencas(base: dict, atual: dict) -> list:
    dif = []
    for cas in sorted(set(base) | set(atual)):
        b, a = base.get(cas, {}), atual.get(cas, {})
        for g in sorted(set(b) | set(a)):
            if g not in a:
                dif.append(f"{cas} gto {g}: sumiu da rodada")
                continue
            if g not in b:
                dif.append(f"{cas} gto {g}: apareceu na rodada")
                continue
            for campo in sorted(set(b[g]) | set(a[g])):
                if b[g].get(campo) != a[g].get(campo):
                    dif.append(f"{cas} gto {g}: {campo} {b[g].get(campo)} -> {a[g].get(campo)}")
    return dif


if __name__ == "__main__":
    atual = tocar_todos()
    arq = os.path.join(rh.PASTA_PADRAO, "baseline.json")
    if "--baseline" in sys.argv:
        json.dump(atual, open(arq, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"linha de base gravada: {sum(len(v) for v in atual.values())} guias "
              f"em {len(atual)} cassete(s)")
        sys.exit(0)
    dif = diferencas(json.load(open(arq, encoding="utf-8")), atual)
    print("\n".join(dif) if dif else "IGUAL: nenhuma decisao mudou")
    sys.exit(1 if dif else 0)
```

```python
# replay_gravar.py
"""Grava cassetes de rodadas DRY reais (nunca anexa).
Uso: python3 replay_gravar.py 388336:07/10/2026 397950:07/10/2026 ...
Rodar nesta maquina, sem ODONTO_PROXY_URL, com GEMINI_API_KEY e SMARTRIS_* no ambiente."""
import os
import sys
import tempfile

import replay_harness as rh


def gravar(conta: str, dia: str) -> str:
    import db
    import esteira
    nome = f"{conta}_{dia.replace('/', '')}"
    c = rh.Cassete(os.path.join(rh.PASTA_PADRAO, nome))
    c.meta.update({"conta": conta, "dia": dia})
    with rh.instalar("gravar", c):
        r = esteira.rodar_esteira(dia, 1, 1, 5, log=print,
                                  gemini_key=os.environ["GEMINI_API_KEY"],
                                  review_dir=tempfile.mkdtemp(), k_attach=1,
                                  dry_run=True, conta=conta,
                                  senha_portal=db.get_portal_senha(conta))
    print(f"[gravado] {nome}: {len(r.get('decisoes') or [])} decisao(oes)")
    return nome


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        conta, dia = arg.split(":", 1)
        gravar(conta, dia)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/adoni/rb-prod && python3 -m pytest -q -p no:warnings test_replay_cassetes.py`
Expected: 1 passed, 1 skipped (ainda não há cassetes)

- [ ] **Step 5: Commit**

```bash
git add replay_gravar.py replay_comparar.py test_replay_cassetes.py
git commit -m "replay: CLIs de gravar/comparar e teste dos cassetes reais"
```

---

### Task 5: Gravar cassetes reais, fixar a linha de base e provar determinismo

**Files:**
- Nenhum arquivo do repositório (cassetes e baseline ficam em `C:\Users\adoni\rb-replay\cassetes\`).

**Interfaces:**
- Consumes: `replay_gravar.py`, `replay_comparar.py`.
- Produces: pasta de cassetes + `baseline.json` usada pelas Fases 2–5.

- [ ] **Step 1: Escolher os dias.** Pegar, no banco, (conta, dia) recentes com mais guias pendentes e variedade (auto, sem_solicitacao, sem_exame, modelo, doc orto). Consulta:

```bash
export $(grep -E "^DATABASE_URL=" "/c/Users/adoni/OneDrive/Documentos/RADIOBRAS/radiobras-extrator/.env" | xargs -d '\n'); export PYTHONIOENCODING=utf-8
python3 -c "
import os, psycopg2
c = psycopg2.connect(os.environ['DATABASE_URL']).cursor()
c.execute(\"\"\"select e.conta, e.dia, count(*), string_agg(distinct ei.categoria, ',')
from execucao_itens ei join execucoes e on e.id=ei.execucao_id
where e.criado_em >= now() - interval '5 days' and not e.dry_run and ei.categoria <> 'ja_anexada'
group by 1,2 order by 3 desc limit 9\"\"\")
for r in c.fetchall(): print(r)"
```

- [ ] **Step 2: Gravar** (um por vez, em background, ~3–8 min cada):

```bash
cd /c/Users/adoni/rb-prod
export $(grep -vE "^ODONTO_PROXY_URL=" "/c/Users/adoni/OneDrive/Documentos/RADIOBRAS/radiobras-extrator/.env" | grep -E "^[A-Z_]+=" | xargs -d '\n'); unset ODONTO_PROXY_URL; export PYTHONIOENCODING=utf-8
nohup python3 replay_gravar.py 397950:07/10/2026 > /c/Users/adoni/rb-replay/gravar.log 2>&1 &
```

Expected: `[gravado] 397950_07102026: N decisao(oes)` com N > 0.

- [ ] **Step 3: Linha de base**

Run: `python3 replay_comparar.py --baseline`
Expected: `linha de base gravada: X guias em Y cassete(s)`

- [ ] **Step 4: Determinismo** — tocar duas vezes seguidas sem mudar código:

Run: `python3 replay_comparar.py && python3 replay_comparar.py`
Expected: `IGUAL: nenhuma decisao mudou` nas duas. Se aparecer diferença sem mudança de código, há não-determinismo (ordem de thread, tempo no motivo): normalizar em `veredito()` com teste e voltar ao Step 3.

- [ ] **Step 5: Sensibilidade** — provar que a rede pega mudança: alterar temporariamente `_DISPENSAM_LAUDO = {"fotografia"}` em `esteira.py`, rodar `python3 replay_comparar.py` (deve listar diferenças se houver guia de modelo nos cassetes; senão trocar por uma regra exercitada, ex. `_JANELA_DIAS`), e desfazer com `git checkout esteira.py`. Registrar o resultado no relatório da fase.

- [ ] **Step 6: Suíte inteira** — `python3 -m pytest -q -p no:warnings` (agora o teste de cassetes roda de verdade nesta máquina). Expected: tudo passando.
