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


def anexos_do_paciente(page, nome: str, cod: str) -> list:
    """Busca o paciente, abre prontuario + anexos, retorna [{id, filename, url}].

    A busca colapsa espacos duplicados (caso ANGELICA OLIVEIRA  LEAHY, 27/07:
    o nome veio do analitico com espaco DUPLO e o #patient_search achava 0
    cards) e, se nao achar nada, tenta de novo tirando o ultimo sobrenome
    (ate 2 tokens). O encurtamento so vale quando ha CODIGO real do paciente
    (o card precisa conter o codigo — _record_href); no fallback por nome
    (cod "WL*", sem prova) NAO se encurta: uma busca mais ampla com aceite de
    card unico poderia abrir o prontuario de OUTRA pessoa."""
    nome_limpo = " ".join(str(nome or "").split())
    tentativas = [nome_limpo]
    toks = nome_limpo.split(" ")
    cod_s = str(cod or "").strip()
    # Encurtar exige codigo REAL e nao-vazio: com cod vazio, o ''.includes()
    # do _record_href e true para QUALQUER card, e a busca cada vez mais larga
    # aceitaria o primeiro paciente que aparecesse (achado do code review 31/07)
    if cod_s and not cod_s.startswith("WL"):
        tentativas += [" ".join(toks[:n]) for n in range(len(toks) - 1, 1, -1)]

    href, n_cards = None, 0
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
        if href:
            break

    if not href:
        # O motivo tem que dizer a VERDADE: 0 cards e 2+ cards sao problemas
        # diferentes e mandam a operadora procurar coisas diferentes.
        if n_cards == 0:
            raise ProntuarioAmbiguo(
                f"paciente {nome_limpo!r} não encontrado no cadastro do PRORADIS — "
                f"conferir se o nome está escrito igual nos dois sistemas")
        raise ProntuarioAmbiguo(
            f"{n_cards} pacientes com o nome {nome_limpo!r} no PRORADIS — não foi "
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

    itens = _abrir_anexos(page, href, cod)
    if gemeos:
        vistos = {(i.get("id"), i.get("filename")) for i in itens}
        for g in gemeos:
            try:
                extras = _abrir_anexos(page, g["href"], g.get("cod") or cod)
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
