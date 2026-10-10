"""Coleta o DESFECHO na RedeUna das guias que NÓS faturamos.

Âncora: db.guias_faturadas_por_nos (ExecucaoItem.faturado). Por guia consulta o
Demonstrativo de Pagamento (pago/glosado/repasse); as glosadas são enriquecidas com
motivo + como-recursar (relatório de glosa) + estado do recurso + prazo (120d orto /
90d demais a contar do repasse). 100% leitura; nenhuma escrita no portal.
"""
import os
import re
from datetime import date, datetime

from extrator_odontoprev import login_odonto
from glosa_extrator import (_abrir_topo, _clicar_subitem, _btn_por_texto, _num_brl,
                            _demo_set_guia, checar_recurso, extrair_unidade)
import db
import desfecho as _d


# Orientação "Como Recursar?" por código de glosa — texto padronizado do Manual do
# Credenciado (capturado do próprio Relatório de Glosa). Fallback genérico no fim.
COMO_RECURSAR = {
    "3052": "Enviar nova imagem da GTO (nítida, completa, sem cortes) em Recurso de "
            "Glosa via Portal Rede UNNA; conferir pedido em papel timbrado, com nome do "
            "beneficiário, dente/região, data e carimbo do dentista.",
    "1733": "Recuperação de valores por pagamento indevido — em geral NÃO recursável; "
            "conferir no Recurso de Glosa se a guia aceita recurso.",
    "2908": "Reanálise já efetuada de forma incorreta — o recurso anterior foi recusado; "
            "revisar a documentação antes de tentar de novo.",
}
COMO_RECURSAR_GENERICO = ("Abrir Recurso de Glosa via Portal Rede UNNA na guia e seguir "
                          "a orientação do relatório para o motivo específico.")

_DT_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")


def _parse_br(dstr):
    try:
        return datetime.strptime(dstr, "%d/%m/%Y").date()
    except Exception:
        return None


def consultar_demo_repasse(page, guia) -> dict:
    """Como glosa_extrator.consultar_demonstrativo, mas captura também a DATA DO
    REPASSE (necessária pro prazo). {tem_dados, bruto, glosado, pago, data_repasse}."""
    out = {"tem_dados": False, "bruto": None, "glosado": None, "pago": False,
           "data_repasse": None}
    # ENTRADA DA GUIA (fix 19/08): o campo "Informe os números das guias" é um
    # chip-input do Vuetify — digitar NÃO basta, precisa ENTER pra virar chip, senão o
    # CONSULTAR não acha guia e volta o formulário vazio (lido como 'aguardando'). E o
    # campo certo é input[type=text] (o primeiro input VISÍVEL é o radio).
    inp = page.query_selector('input[type="text"]')
    if not inp:
        return out
    try:
        inp.evaluate("el=>el.focus()")
        page.wait_for_timeout(120)
        page.keyboard.type(str(guia), delay=60)
        page.wait_for_timeout(200)
        page.keyboard.press("Enter")     # commita o número como chip
        page.wait_for_timeout(400)
    except Exception:
        return out
    page.mouse.move(1100, 650); page.wait_for_timeout(150)
    btn = _btn_por_texto(page, "CONSULTAR")
    if btn:
        try:
            btn.click(timeout=6000)
        except Exception:
            btn.click(force=True)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    corpo = re.sub(r"\s+", " ", page.inner_text("body") or "")
    mb = re.search(r"bruto[:\s]*R\$\s*([\d\.,]+)", corpo, re.I)
    mg = re.search(r"glosado[:\s]*R\$\s*([\d\.,]+)", corpo, re.I)
    out["bruto"] = _num_brl(mb.group(1)) if mb else None
    out["glosado"] = _num_brl(mg.group(1)) if mg else None
    sem_pg = "não há dados" in corpo.lower() or "nao ha dados" in corpo.lower()
    out["tem_dados"] = bool((out["bruto"] or 0) > 0 or (out["glosado"] or 0) > 0)
    out["pago"] = bool(out["tem_dados"] and not sem_pg)
    # data do repasse: procura perto de 'repasse/pagamento/crédito'
    md = re.search(r"(?:repasse|pagamento|cr[eé]dito|compet[eê]ncia)[^\d]{0,30}(\d{2}/\d{2}/\d{4})",
                   corpo, re.I)
    if not md:
        md = _DT_RE.search(corpo)   # fallback: primeira data da tela
    out["data_repasse"] = md.group(1) if md else None
    nb = _btn_por_texto(page, "NOVA BUSCA")
    if nb:
        try:
            nb.click(force=True)
        except Exception:
            pass
        page.wait_for_timeout(1000)
    return out


_GUIA_RE = re.compile(
    r"Guia:\s*(\d+).*?Valor Liberado:\s*R\$\s*([\d\.,]+)\s*Valor Glosado:\s*R\$\s*([\d\.,]+)"
    r"\s*(Pagamento [^\n]*)?", re.S | re.I)

# Guias por consulta. O campo aceita varios numeros (chip por Enter) e a tela devolve
# a relacao por guia. Uma consulta por guia levava 8-10s: 1.400 guias da 388336 =
# mais de 3h, a atualizacao diaria nunca terminava (nada gravado desde 25/09) e o
# job pendurado travava o deploy. Em lote sao ~30 consultas.
DEMO_LOTE = 40


def ler_demo_lote(texto: str) -> dict:
    """Texto da tela do Demonstrativo (relacao de GTOs expandida) ->
    {"guias": {gto: demo}, "datas_pagamento": [...]}.

    demo = {tem_dados, bruto, glosado, liberado, situacao, pago, data_repasse}. bruto
    = liberado + glosado (valor original da guia). Glosa integral vem com liberado 0:
    antes isso virava "sem dados" e a guia glosada aparecia como AGUARDANDO (ELIANE,
    194474509, R$ 102,22 glosados). pago/data_repasse ficam para quem chama: a tabela
    de pagamento e do lote inteiro, nao de cada guia."""
    from glosa_extrator import _num_brl
    t = texto or ""
    pag = ""
    m = re.search(r"Data de Pagamento(.*?)(?:Os valores descritos|Clique na seta|$)", t, re.S | re.I)
    if m:
        pag = m.group(1)
    datas = sorted(set(_DT_RE.findall(pag)))
    guias = {}
    for g, lib, glo, sit in _GUIA_RE.findall(t):
        lib_v, glo_v = _num_brl(lib) or 0.0, _num_brl(glo) or 0.0
        guias[g] = {"tem_dados": True, "liberado": lib_v, "glosado": glo_v,
                    "bruto": round(lib_v + glo_v, 2), "situacao": (sit or "").strip(),
                    "pago": False, "data_repasse": None}
    return {"guias": guias, "datas_pagamento": datas}


def _demo_expandir(page):
    """Abre os paineis 'Dentista' (relacao de GTOs) que estiverem fechados."""
    try:
        page.evaluate("""() => document.querySelectorAll(
            '.v-expansion-panel-header, .v-expansion-panel-title, button[aria-expanded="false"]'
        ).forEach(h => { if (h.getAttribute('aria-expanded') !== 'true') h.click(); })""")
    except Exception:
        pass
    page.wait_for_timeout(1500)
    if "Valor Liberado" not in (page.inner_text("body") or ""):
        try:
            page.locator("text=Dentista").last.click()
            page.wait_for_timeout(1500)
        except Exception:
            pass


def consultar_demo_lote(page, guias) -> dict:
    """Consulta varias guias de uma vez. Devolve o mesmo que ler_demo_lote."""
    if not _demo_form_visivel(page):
        _demo_voltar_ao_form(page)
    inp = page.query_selector('input[type="text"]')
    if not inp:
        return {"guias": {}, "datas_pagamento": []}
    inp.evaluate("el=>el.focus()")
    page.wait_for_timeout(120)
    for g in guias:
        page.keyboard.type(str(g), delay=30)
        page.keyboard.press("Enter")        # cada numero vira um chip
        page.wait_for_timeout(120)
    page.mouse.move(1100, 650); page.wait_for_timeout(150)
    btn = _btn_por_texto(page, "CONSULTAR")
    if btn:
        try:
            btn.click(timeout=6000)
        except Exception:
            btn.click(force=True)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    _demo_expandir(page)
    r = ler_demo_lote(page.inner_text("body"))
    _demo_voltar_ao_form(page)
    return r


def _demo_form_visivel(page) -> bool:
    """Formulario na tela = botao CONSULTAR presente e sem NOVA BUSCA (tela de
    resultado). O campo da guia nao serve de sinal: e um input de altura 0 do
    Vuetify, nunca 'visivel'."""
    try:
        return bool(_btn_por_texto(page, "CONSULTAR")) and not _btn_por_texto(page, "NOVA BUSCA")
    except Exception:
        return False


def _demo_voltar_ao_form(page):
    """Volta ao formulario para o proximo lote. Com 40 guias expandidas a pagina fica
    rolada para baixo e o NOVA BUSCA nao volta (o 2o lote saia vazio, medido 10/10):
    sobe a pagina, clica, e se ainda assim nao voltou reabre o Demonstrativo."""
    try:
        page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass
    page.wait_for_timeout(300)
    nb = _btn_por_texto(page, "NOVA BUSCA")
    if nb:
        try:
            nb.click(timeout=6000)
        except Exception:
            try:
                nb.click(force=True)
            except Exception:
                pass
        page.wait_for_timeout(1200)
    if not _demo_form_visivel(page):
        _abrir_topo(page, "Financeiro"); _clicar_subitem(page, "DEMONSTRATIVO")
        page.wait_for_timeout(1200)
        page.mouse.move(1100, 400); page.mouse.click(1100, 400); page.wait_for_timeout(500)


def _resolver_pagamento(lote_r, consultar_um) -> dict:
    """Pagamento por guia a partir do lote. Tabela vazia: ninguem pago ainda. Uma
    data so: todas as autorizadas do lote foram pagas nela. Mais de uma: so a
    consulta individual diz qual data e de qual guia."""
    datas = lote_r["datas_pagamento"]
    out = {}
    for g, d in lote_r["guias"].items():
        d = dict(d)
        if d["liberado"] > 0 and datas:
            if len(datas) == 1:
                d["pago"], d["data_repasse"] = True, datas[0]
            else:
                um = consultar_um(g) or {}
                ds = um.get("datas_pagamento") or []
                if len(ds) == 1:
                    d["pago"], d["data_repasse"] = True, ds[0]
        out[g] = d
    return out


def _como_recursar(glosa_cod):
    return COMO_RECURSAR.get(glosa_cod, COMO_RECURSAR_GENERICO)


def extrair_desfechos_conta(pw, conta, unidade, guias, dia_str, hoje=None, log=print,
                            checar_demo=True) -> list:
    """Desfecho das `guias` (lista de dicts {gto,paciente,dia_faturado}) de UMA conta.
    dia_str = data-fim p/ o relatório de glosa (period até hoje). Retorna itens prontos
    pra db.salvar_desfechos."""
    hoje = hoje or date.today()
    senha = db.get_portal_senha(conta)

    # 1) glosa da unidade: SÓ o PDF (motivo/código por ficha). O recurso NÃO é checado
    # aqui (o extrair_unidade checaria as N glosadas do período inteiro) — checamos
    # depois SÓ as nossas glosadas, que são poucas. Ganho grande em escala.
    glosa_por_ficha = {}
    try:
        r = extrair_unidade(pw, conta, unidade, dia_str, "_diag_glosa",
                            checar_recursos=False, checar_demonstrativo=False, log=log)
        for e in r.get("eventos", []):
            glosa_por_ficha.setdefault(str(e["ficha"]), e)  # 1º evento por ficha
    except Exception as e:
        log(f"[{unidade}] glosa bulk falhou: {str(e)[:80]}")

    # 2) Demonstrativo por guia -> status financeiro
    def _mk(g, demo):
        gt = str(g["gto"])
        # GLOSADA vem do RELATÓRIO DE GLOSA (confiável, independe do pagamento), NÃO do
        # Demonstrativo (que só popula após o repasse). Sem isso, glosada com repasse
        # pendente cairia em 'aguardando' e o motivo/recurso sumiria.
        st = "GLOSADA" if gt in glosa_por_ficha else _d.classificar_desfecho(False, demo)
        return {"conta": conta, "unidade": unidade, "gto": gt,
                "paciente": g.get("paciente"), "dia_faturado": g.get("dia_faturado"), "status": st,
                "valor_bruto": (demo or {}).get("bruto"), "valor_glosado": (demo or {}).get("glosado"),
                "valor_pago": ((demo or {}).get("bruto") or 0) - ((demo or {}).get("glosado") or 0)
                if demo and demo.get("tem_dados") else None,
                "data_repasse": (demo or {}).get("data_repasse")}

    itens = []
    if checar_demo:
        b, c, page = login_odonto(pw, conta, senha)
        try:
            _abrir_topo(page, "Financeiro"); _clicar_subitem(page, "DEMONSTRATIVO")
            page.wait_for_timeout(1200)
            page.mouse.move(1100, 400); page.mouse.click(1100, 400); page.wait_for_timeout(500)
            for i in range(0, len(guias), DEMO_LOTE):
                bloco = guias[i:i + DEMO_LOTE]
                try:
                    r = consultar_demo_lote(page, [str(g["gto"]) for g in bloco])
                    if not r["guias"]:      # lote vazio = tela fora do lugar: refaz 1x
                        _abrir_topo(page, "Financeiro"); _clicar_subitem(page, "DEMONSTRATIVO")
                        page.wait_for_timeout(1200)
                        r = consultar_demo_lote(page, [str(g["gto"]) for g in bloco])
                    demos = _resolver_pagamento(
                        r, lambda gt: consultar_demo_lote(page, [gt]))
                except Exception as e:
                    log(f"[{unidade}]   lote do demonstrativo falhou: {str(e)[:80]}")
                    demos = {}
                for g in bloco:
                    itens.append(_mk(g, demos.get(str(g["gto"]))))
                log(f"[{unidade}]   demonstrativo {min(i + DEMO_LOTE, len(guias))}/{len(guias)}"
                    f" ({len(demos)} com dados)")
        finally:
            try:
                b.close()
            except Exception:
                pass
    else:
        # MODO RÁPIDO: sem Demonstrativo (repasse ainda não processou). Classifica só
        # GLOSADA (relatório) x AGUARDANDO. O update diário completo preenche o pago.
        itens = [_mk(g, None) for g in guias]
        log(f"[{unidade}] modo rápido (sem demonstrativo): {len(itens)} guia(s)")

    # 3) Recurso SÓ das NOSSAS glosadas (poucas) — recursável x sem-glosado
    minhas_glosadas = [it["gto"] for it in itens if it["status"] == "GLOSADA"]
    recurso = {}
    if minhas_glosadas:
        log(f"[{unidade}] checando recurso de {len(minhas_glosadas)} glosada(s) nossa(s)...")
        b, c, page = login_odonto(pw, conta, senha)
        try:
            _abrir_topo(page, "Recurso de Glosa"); _clicar_subitem(page, "RECURSO DE GLOSA")
            for gto in minhas_glosadas:
                try:
                    recurso[gto] = checar_recurso(page, gto)
                except Exception:
                    recurso[gto] = "INDEFINIDO"
                try:
                    _abrir_topo(page, "Recurso de Glosa"); _clicar_subitem(page, "RECURSO DE GLOSA")
                except Exception:
                    pass
        finally:
            try:
                b.close()
            except Exception:
                pass

    # 4) enriquece as glosadas com motivo + como-recursar + estado + prazo
    for item in itens:
        if item["status"] != "GLOSADA":
            continue
        gto = item["gto"]
        ev = glosa_por_ficha.get(gto, {})
        evtxt = f"{ev.get('evento', '')} {ev.get('glosa_motivo', '')}"
        orto = _d.eh_ortodontia(evtxt)
        pr = _d.prazo_recurso(_parse_br(item["data_repasse"]), orto, hoje)
        item.update({
            "glosa_cod": ev.get("glosa_cod", ""),
            "glosa_motivo": ev.get("glosa_motivo", ""),
            "como_recursar": _como_recursar(ev.get("glosa_cod", "")),
            "recurso_estado": "PRESCRITO" if pr["prescrito"] else recurso.get(gto, "NAO_CHECADO"),
            "ortodontia": orto,
            "prazo_limite": pr["data_limite"].strftime("%d/%m/%Y") if pr["data_limite"] else "",
            "prazo_dias": pr["dias_restantes"], "prescrito": pr["prescrito"],
        })
    return itens
