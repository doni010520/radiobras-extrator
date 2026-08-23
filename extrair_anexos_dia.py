"""
extrair_anexos_dia.py — UM LOGIN, todos os pacientes do dia.

Para cada paciente REDE UNNA do dia:
  busca por nome -> abre Prontuario (card) -> abre Anexos -> baixa todos os
  arquivos anexados -> sonda cada arquivo (pdf/imagem, texto, codigo de barras).

Saida: _anexos_<data>/<cod>_<slug>/<arquivos> + manifest.json com a sondagem.
Objetivo imediato: dataset real para calibrar o detector de GTO (deterministico).
"""
import io
import json
import os
import re
import time

import cv2
import fitz  # PyMuPDF
import numpy as np
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from extrator_pacientes_analitico import BASE_URL as BASE, get_credentials
from extrator_arquivos import _login_playwright, _get_relatorio_analitico, slug
from config import CONVENIOS, SEGMENTOS

DATA = "03/06/2026"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "_anexos_" + DATA.replace("/", ""))


# ── Sondagem deterministica de cada arquivo ───────────────────────────────────

def _barcodes(img) -> list:
    """Le codigos de barras 1D via cv2.barcode (sem zbar). Robusto a versoes."""
    try:
        det = cv2.barcode.BarcodeDetector()
        res = det.detectAndDecode(img)
    except Exception:
        return []
    # versoes retornam (ok, info, types, pts) ou (info, types, pts)
    info = None
    if isinstance(res, tuple):
        for el in res:
            if isinstance(el, (list, tuple)) and el and isinstance(el[0], str):
                info = el
                break
    return [s for s in (info or []) if s]


def sondar(nome_arq: str, body: bytes) -> dict:
    ext = os.path.splitext(nome_arq)[1].lower().lstrip(".")
    info = {"arquivo": nome_arq, "ext": ext, "bytes": len(body), "kind": "?"}
    head = body[:5]

    if head[:4] == b"%PDF":
        info["kind"] = "pdf"
        try:
            doc = fitz.open(stream=body, filetype="pdf")
            txt = "".join(p.get_text() for p in doc)
            info["n_pages"] = doc.page_count
            info["text_len"] = len(txt.strip())
            info["has_text"] = info["text_len"] > 20
            info["text_sample"] = re.sub(r"\s+", " ", txt[:400]).strip()
            # Se for PDF escaneado (sem texto), rasteriza pag.1 e busca barcode
            if not info["has_text"] and doc.page_count:
                pix = doc.load_page(0).get_pixmap(dpi=150)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
                img = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR) if pix.n >= 3 else arr
                info["barcodes"] = _barcodes(img)
            doc.close()
        except Exception as e:
            info["erro"] = f"pdf: {e}"
    elif head[:2] == b"\xff\xd8" or head[:8] == b"\x89PNG\r\n\x1a\n" or ext in ("png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff"):
        info["kind"] = "image"
        try:
            arr = np.frombuffer(body, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                h, w = img.shape[:2]
                info["dims"] = [w, h]
                info["barcodes"] = _barcodes(img)
            else:
                info["erro"] = "imdecode falhou"
        except Exception as e:
            info["erro"] = f"img: {e}"
    return info


# ── Navegacao de anexos por paciente ──────────────────────────────────────────

def _record_href(page, cod: str):
    """Href do botao Prontuario do card cujo texto contem o codigo `cod`.

    FALHA FECHADO: antes, se o codigo nao fosse encontrado, devolvia links[0] —
    o PRIMEIRO card da busca. Como o fallback da esteira usa um codigo sintetico
    ("WL<accession>"), que NUNCA aparece no card, esse caminho abria o prontuario
    de outra pessoa — e dali saiam campo 49, dispensa de laudo e a solicitacao.
    Agora: sem correspondencia, so aceita quando ha UM UNICO card (nao ha o que
    confundir). Com 2+ cards devolve None -> vira pendencia, nunca paciente errado.
    """
    return page.evaluate("""(cod) => {
        const links = [...document.querySelectorAll('a.prontuario')];
        if (cod) {   // cod vazio: ''.includes() casaria com QUALQUER card
            for (const a of links) {
                let node = a, txt = '';
                for (let i = 0; i < 6 && node; i++) { node = node.parentElement; if (node) txt += ' ' + node.innerText; }
                if (txt.includes(cod)) return {href: a.href, n: links.length};
            }
        }
        return {href: links.length === 1 ? links[0].href : null, n: links.length};
    }""", cod)


def _parse_cards_cpf(html: str) -> list:
    """Cards do resultado de search_patient_list -> [{cod, pid}].

    cod = data-pat-id (numero do prontuario). pid = argumento de
    load_patient_profile(...) — o mesmo patient_id que view_attachments usa
    (comprovado por baixar_solicitacoes._patient_id, em producao).

    Um CPF pode devolver 2+ cards: prontuarios DUPLICADOS do mesmo paciente.
    Caso ADAILDES FIUZA DOS SANTOS (29/07): CPF -> 20040659 + 20040640."""
    out = []
    for el in BeautifulSoup(html or "", "lxml").select("[data-pat-id]"):
        cod = (el.get("data-pat-id") or "").strip()
        if not cod:
            continue
        m = re.search(r"load_patient_profile\('([^']+)'\)", str(el))
        out.append({"cod": cod, "pid": m.group(1) if m else None})
    return out


def _norm_nasc(s) -> str:
    """Normaliza nascimento p/ DD/MM/AAAA. Aceita AAAA-MM-DD (OdontoPrev,
    beneficiario.dataNascimento) e DD/MM/AAAA (card do PRORADIS). Vazio se nao
    reconhecer."""
    s = str(s or "").strip()[:10]
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    if re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s):
        return s
    return ""


def _cards_por_nascimento(cards, nasc_guia) -> list:
    """Filtra os cards cujo nascimento BATE com o da guia — desempate de homonimo
    (caso FILIPE: dois "Felipe" com nascimentos diferentes). Guia SEM nascimento
    -> devolve TODOS (sem data nao ha desempate seguro; quem chama cai na logica
    anterior). Card sem nascimento nunca casa. NAO inventa desempate."""
    alvo = _norm_nasc(nasc_guia)
    if not alvo:
        return list(cards or [])
    return [c for c in (cards or []) if _norm_nasc(c.get("nascimento")) == alvo]


def _parse_anexos_view(html: str, cod: str) -> list:
    """HTML do view_attachments -> [{id, filename, url}]. Mesma leitura de
    _abrir_anexos: .attachment-item com data-id/data-filename; a url vem do
    <a download_attachment> ou e montada a partir de id+cod."""
    itens = []
    for div in BeautifulSoup(html or "", "lxml").select(".attachment-item"):
        aid = div.get("data-id", "")
        fn = div.get("data-filename", "")
        a = div.select_one("a[href*='download_attachment']")
        url = a["href"] if a else f"{BASE}/patients/download_attachment/{aid}/{cod}"
        itens.append({"id": aid, "filename": fn, "url": url})
    return itens


def buscar_prontuarios_por_cpf(sess, cpf) -> list:
    """CPF -> prontuarios [{cod, pid}] via search_patient_list (o endpoint
    INDEXA CPF; descoberta 05/08). Normaliza p/ digitos e so bate no servidor
    com 11 digitos: CPF vazio/malformado nao pode virar uma busca ampla que
    casaria com paciente errado."""
    dig = re.sub(r"\D", "", cpf or "")
    if len(dig) != 11:
        return []
    # Falha de rede na busca por CPF NAO pode bloquear a guia: degrada p/ [] e
    # quem chama cai no fallback por nome — mesmo tratamento do nao-200 (M1).
    try:
        r = sess.get(f"{BASE}/patients/search_patient_list/search_patient_list/",
                     params={"limit": 24, "input": dig}, timeout=30)
    except Exception:
        return []
    if getattr(r, "status_code", 0) != 200:
        return []
    return _parse_cards_cpf(r.text)


def _listar_anexos_http(sess, pid, cod) -> list:
    """Anexos de UM prontuario via view_attachments (HTTP puro)."""
    r = sess.post(f"{BASE}/patients/view_attachments",
                  data={"patient_id": pid}, timeout=30)
    if getattr(r, "status_code", 0) != 200:
        return []
    return _parse_anexos_view(r.text, cod)


def anexos_por_cpf(sess, cpf):
    """Anexos de TODOS os prontuarios que o CPF retornar — uniao com dedupe por
    (id, filename). Devolve (itens, prontuarios). CPF sem match -> ([], []), e
    quem chama cai no fallback por nome. A uniao substitui o _gemeos_de: o CPF
    ja junta os prontuarios duplicados (caso ADAILDES / IRAMAIA)."""
    pronts = buscar_prontuarios_por_cpf(sess, cpf)
    if not pronts:
        return [], []
    vistos, itens = set(), []
    for p in pronts:
        if not p.get("pid"):
            continue
        for it in _listar_anexos_http(sess, p["pid"], p.get("cod", "")):
            k = (it.get("id"), it.get("filename"))
            if k in vistos:
                continue
            vistos.add(k); itens.append(it)
    return itens, pronts


def resolver_anexos(cpf, buscar_cpf_fn, buscar_nome_fn):
    """CPF-first, nome-fallback. Devolve (itens, fonte), fonte in {'cpf','nome'}.

    Se o CPF achou prontuario(s), CONFIA nele mesmo com zero anexos — nao cai no
    nome (o fallback por nome poderia abrir prontuario de homonimo). So o caso
    'CPF nao achou ninguem' (cadastro do PRORADIS sem CPF) usa o nome, como
    antes: NUNCA regride. `fonte` sobe pro log e mede a taxa real de acerto por
    CPF ja na 1a rodada — sem scraping extra."""
    if cpf:
        itens, pronts = buscar_cpf_fn()
        if pronts:
            return itens, "cpf"
    return buscar_nome_fn(), "nome"


class ProntuarioAmbiguo(Exception):
    """A busca por nome nao permitiu identificar UM prontuario com seguranca.
    Melhor parar do que ler o prontuario de outra pessoa."""


def _nome_norm_simples(s) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.upper().split())


def _cards_da_busca(page):
    """Todos os cards do resultado da busca de pacientes:
    {href, nome, nascimento, cod}. O card e o MENOR ancestral do link
    'Prontuario' que contem o texto 'Nascimento' (nao atravessa outros cards)."""
    return page.evaluate(r"""() => {
        const out = [];
        for (const a of document.querySelectorAll('a.prontuario')) {
            let card = null, n = a;
            for (let i = 0; i < 6 && n; i++) {
                n = n.parentElement;
                if (n && /Nascimento/.test(n.innerText)) { card = n; break; }
            }
            const txt = card ? card.innerText : '';
            const nasc = (txt.match(/Nascimento:\s*([0-9\/]+)/) || [])[1] || '';
            const cod = (txt.match(/Prontu[aá]rio:\s*(\d+)/) || [])[1] || '';
            const nome = (txt.split('\n').map(s => s.trim())
                .find(s => s && !/^(Perfil|Prontu|Nascimento|Sexo|Senha|Telefone|Novo|\+|Ú|U)/.test(s)) || '');
            out.push({href: a.href, nome, nascimento: nasc, cod});
        }
        return out;
    }""")


def _gemeos_de(cards, href_principal):
    """Prontuarios DUPLICADOS do mesmo paciente na busca.

    Caso IRAMAIA MACIEL LOPES DE SOUZA (27/07, GTO 195441968): a paciente tem
    DOIS prontuarios no PRORADIS — 20053338 e 20031156 — com o MESMO nome e o
    MESMO nascimento. O exame estava registrado num deles (o que o codigo do
    analitico aponta) e o pedido do dentista foi anexado NO OUTRO; o robo lia
    so o primeiro e reprovava com 'nenhum documento no nome deste paciente'.

    Duplicata comprovada = nome normalizado IDENTICO + nascimento IDENTICO e
    nao-vazio. Homonimo de verdade tem nascimento diferente e fica de fora;
    card 'Novo Paciente' (sem nascimento) tambem. Maximo 2 gemeos."""
    principal = next((c for c in cards or [] if c.get("href") == href_principal), None)
    if not principal or not principal.get("nascimento"):
        return []
    nome = _nome_norm_simples(principal.get("nome"))
    if not nome:
        return []
    out = []
    for c in cards:
        if (c.get("href") and c["href"] != href_principal
                and c.get("nascimento") == principal["nascimento"]
                and _nome_norm_simples(c.get("nome")) == nome):
            out.append(c)
    return out[:2]


def _card_wl_por_nome_nascimento(cards, nome_guia, nascimento):
    """Site-2 (caminho WL, sem codigo real de prontuario): escolhe o card do
    resultado da busca cujo NASCIMENTO bate com o da guia E o nome e compativel
    (_nomes_compat — mesma trava do site-1, rejeita irmao/sobrenome diferente).

    TRAVA DURA: sem nascimento da guia, ou sem card com nascimento IGUAL, retorna
    None (nao aceita nem com nome identico). So devolve se sobrar EXATAMENTE UM —
    dois com mesmo nome+nascimento (gemeo real) fica ambiguo e nao escolhe. Isso
    cobre o cadastro com nome do meio a mais (MATEUS DA SILVA _MONTEIRO_ DE NOVAES)
    sem risco de abrir o prontuario de outra pessoa."""
    nn = _norm_nasc(nascimento)
    if not nn:
        return None
    from esteira import _nomes_compat   # lazy: esteira importa este modulo (evita circular)
    uniq = {}
    for c in cards or []:
        uniq[c.get("cod") or c.get("href")] = c
    casam = [c for c in uniq.values()
             if _norm_nasc(c.get("nascimento")) == nn
             and _nomes_compat(c.get("nome", ""), nome_guia)]
    return casam[0] if len(casam) == 1 else None


_CONECTIVOS_BUSCA = {"DE", "DA", "DO", "DAS", "DOS", "E", "D"}


def _termos_de_busca(nome_limpo: str, cod_s: str, tem_nascimento: bool = False) -> list:
    """Termos que a busca do #patient_search vai tentar, do mais especifico ao mais amplo.

    Encurtar pelo prefixo resolve o cadastro com sobrenome a mais (ANGELICA OLIVEIRA
    LEAHY, 27/07), mas encurtar SEM olhar o que sobra produz busca inutil: 'MARIA DE
    FATIMA LAMOEDO' virava 'MARIA DE' — um prenome comunissimo mais uma preposicao —
    e o PRORADIS devolvia dezenas de cards. A guia 196370003 morreu assim sete vezes.

    Regra: um termo encurtado precisa manter 2 tokens SIGNIFICATIVOS (conectivo nao
    conta). As travas antigas continuam: sem codigo real nao encurta (com cod vazio
    qualquer card 'contem' o codigo — code review 31/07), e 'WL*' so encurta com
    nascimento, porque so ali a aceitacao exige nascimento igual + card unico."""
    nome_limpo = " ".join(str(nome_limpo or "").split())
    if not nome_limpo:
        return []
    termos = [nome_limpo]
    cod_s = str(cod_s or "").strip()
    pode_encurtar = (bool(cod_s) and not cod_s.startswith("WL")) or                     (cod_s.startswith("WL") and bool(tem_nascimento))
    if not pode_encurtar:
        return termos
    toks = nome_limpo.split(" ")
    for n in range(len(toks) - 1, 1, -1):
        prefixo = toks[:n]
        significativos = [t for t in prefixo
                          if t.upper() not in _CONECTIVOS_BUSCA and len(t) > 1]
        if len(significativos) < 2:
            break          # daqui pra baixo so fica mais amplo; nao adianta seguir
        termos.append(" ".join(prefixo))
    return termos


def anexos_do_paciente(page, nome: str, cod: str, nascimento=None) -> list:
    """Busca o paciente, abre prontuario + anexos, retorna [{id, filename, url}].

    A busca colapsa espacos duplicados (caso ANGELICA OLIVEIRA  LEAHY, 27/07:
    o nome veio do analitico com espaco DUPLO e o #patient_search achava 0
    cards) e, se nao achar nada, tenta de novo tirando o ultimo sobrenome
    (ate 2 tokens). O encurtamento so vale quando ha CODIGO real do paciente
    (o card precisa conter o codigo — _record_href); no fallback por nome
    (cod "WL*", sem prova) NAO se encurta: uma busca mais ampla com aceite de
    card unico poderia abrir o prontuario de OUTRA pessoa."""
    nome_limpo = " ".join(str(nome or "").split())
    cod_s = str(cod or "").strip()
    cod_efetivo = cod    # cod usado p/ abrir anexos; vira o cod REAL se o nascimento desempatar
    tentativas = _termos_de_busca(nome_limpo, cod_s, bool(_norm_nasc(nascimento)))

    href, n_cards = None, 0
    # Contagem da busca pelo NOME COMPLETO. `n_cards` e reatribuido a cada tentativa,
    # entao no fim do laco ele guarda o resultado da busca mais CURTA — e a mensagem
    # de erro anunciava esse numero ao lado do nome INTEIRO. Nao havia 24 pacientes
    # chamados 'MARIA DE FATIMA LAMOEDO'; os 24 eram de 'MARIA DE'.
    n_cards_cheio = None
    for busca in tentativas:
        page.goto(f"{BASE}/patients", wait_until="networkidle")
        page.wait_for_timeout(1200)
        campo = page.query_selector("#patient_search")
        campo.click(); campo.fill(busca)
        page.wait_for_timeout(2200)
        page.keyboard.press("Enter")
        page.wait_for_timeout(2500)
        r = _record_href(page, cod) or {}
        href, n_cards = r.get("href"), r.get("n", 0)
        if n_cards_cheio is None:
            n_cards_cheio = n_cards          # a 1a tentativa e sempre o nome cheio
        if href:
            break

    # DESEMPATE POR NASCIMENTO (caso FILIPE: dois "Felipe Silva dos Santos") — SO
    # quando a busca por nome ficou AMBIGUA (2+ cards, nenhum resolvido por codigo)
    # e a guia trouxe o nascimento. Escolhe o card cujo nascimento BATE; so aceita
    # se sobrar UM. Sem nascimento, empate ou nenhum: cai no erro de sempre — NAO
    # inventa desempate. Nascimento vem do /v1/gto/detalhada (OdontoPrev).
    if not href and (n_cards or 0) >= 2 and _norm_nasc(nascimento):
        casam_nasc = _cards_por_nascimento(_cards_da_busca(page), nascimento)
        if len(casam_nasc) == 1:
            href = casam_nasc[0].get("href")
            cod_efetivo = casam_nasc[0].get("cod") or cod
            cod_s = str(cod_efetivo or "").strip()

    # SITE-2 (WL + nascimento): o cod 'WL' nunca casa por codigo. Se a busca (cheia
    # ou encurtada) achou o cadastro com nome do meio a mais, aceita SO com
    # nascimento igual + nome compativel + card unico (trava dura contra outra pessoa).
    if not href and cod_s.startswith("WL"):
        try:
            _cd = _card_wl_por_nome_nascimento(_cards_da_busca(page), nome_limpo, nascimento)
        except Exception:
            _cd = None
        if _cd:
            href = _cd.get("href")
            cod_efetivo = _cd.get("cod") or cod
            cod_s = str(cod_efetivo or "").strip()

    if not href:
        # O motivo tem que dizer a VERDADE: 0 cards e 2+ cards sao problemas
        # diferentes e mandam a operadora procurar coisas diferentes.
        _n = n_cards_cheio if n_cards_cheio is not None else n_cards
        if _n == 0:
            raise ProntuarioAmbiguo(
                f"paciente {nome_limpo!r} não encontrado no cadastro do PRORADIS — "
                f"conferir se o nome está escrito igual nos dois sistemas")
        raise ProntuarioAmbiguo(
            f"{_n} paciente(s) com o nome {nome_limpo!r} no PRORADIS — não foi "
            f"possível identificar o prontuário com segurança")
    # PRONTUARIO DUPLICADO (caso IRAMAIA, 27/07): antes de sair da tela de
    # busca, verifica se ha outro card do MESMO paciente (nome + nascimento
    # identicos) — o pedido pode ter sido anexado no prontuario duplicado.
    gemeos = []
    if cod_s and not cod_s.startswith("WL"):
        try:
            gemeos = _gemeos_de(_cards_da_busca(page), href)
        except Exception:
            gemeos = []

    itens = _abrir_anexos(page, href, cod_efetivo)
    if gemeos:
        vistos = {(i.get("id"), i.get("filename")) for i in itens}
        for g in gemeos:
            try:
                extras = _abrir_anexos(page, g["href"], g.get("cod") or cod_efetivo)
            except Exception:
                continue
            for it in extras:
                k = (it.get("id"), it.get("filename"))
                if k not in vistos:
                    vistos.add(k)
                    itens.append(it)
    return itens


def _abrir_anexos(page, href, cod) -> list:
    """Abre UM prontuario (href) e lista os anexos dele."""
    page.goto(href, wait_until="networkidle")
    page.wait_for_timeout(1500)

    btn = page.query_selector("#patient_attachments")
    if not btn:
        return []
    btn.click()
    page.wait_for_timeout(2500)

    html = page.evaluate("""() => {
        const w = document.querySelector('.attachment-list');
        return w ? w.outerHTML : '';
    }""")
    soup = BeautifulSoup(html, "lxml")
    itens = []
    for div in soup.select(".attachment-item"):
        aid = div.get("data-id", "")
        fn = div.get("data-filename", "")
        a = div.select_one("a[href*='download_attachment']")
        url = a["href"] if a else f"{BASE}/patients/download_attachment/{aid}/{cod}"
        itens.append({"id": aid, "filename": fn, "url": url})
    return itens


# ── Orquestrador ──────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUT, exist_ok=True)
    email, password = get_credentials()
    manifest = {"data": DATA, "pacientes": []}

    with sync_playwright() as pw:
        browser, ctx, page = _login_playwright(pw, email, password)
        try:
            print("[A] Relatorio analitico do dia (mesma sessao)...")
            df = _get_relatorio_analitico(page, CONVENIOS, SEGMENTOS, DATA)
            cod_col = "Cód. Pac" if "Cód. Pac" in df.columns else df.columns[1]
            nome_col = "Paciente" if "Paciente" in df.columns else df.columns[2]

            # pacientes unicos (cod -> nome mais longo)
            pac = {}
            for _, r in df.iterrows():
                c = str(r[cod_col]).strip()
                n = str(r[nome_col]).strip()
                if c not in pac or len(n) > len(pac[c]):
                    pac[c] = n
            pacientes = sorted(pac.items())
            print(f"   {len(pacientes)} pacientes.")

            for idx, (cod, nome) in enumerate(pacientes, 1):
                print(f"\n[{idx}/{len(pacientes)}] {nome} ({cod})", flush=True)
                entry = {"cod": cod, "nome": nome, "anexos": []}
                try:
                    itens = anexos_do_paciente(page, nome, cod)
                    print(f"   {len(itens)} anexos")
                    # cookies atuais p/ download via requests
                    cj = {c["name"]: c["value"] for c in ctx.cookies()}
                    sess = requests.Session()
                    sess.cookies.update(cj)
                    sess.headers.update({"User-Agent": "Mozilla/5.0",
                                         "Referer": f"{BASE}/patients"})
                    pasta = os.path.join(OUT, f"{cod}_{slug(nome)}")
                    os.makedirs(pasta, exist_ok=True)
                    for it in itens:
                        try:
                            r = sess.get(it["url"], timeout=60)
                            body = r.content
                            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", it["filename"]) or it["id"]
                            with open(os.path.join(pasta, safe), "wb") as f:
                                f.write(body)
                            info = sondar(it["filename"], body)
                            info["id"] = it["id"]
                            entry["anexos"].append(info)
                            bc = info.get("barcodes")
                            print(f"     - {it['filename']} [{info['kind']}]"
                                  + (f" txt={info.get('text_len')}" if info["kind"] == "pdf" else "")
                                  + (f" barcode={bc}" if bc else ""))
                        except Exception as e:
                            entry["anexos"].append({"arquivo": it["filename"], "erro": str(e)})
                            print(f"     - {it['filename']} ERRO download: {e}")
                except Exception as e:
                    entry["erro"] = str(e)
                    print(f"   ERRO: {e}")
                manifest["pacientes"].append(entry)
        finally:
            browser.close()

    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] manifest.json salvo em {OUT}")


if __name__ == "__main__":
    main()
