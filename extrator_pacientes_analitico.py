"""
Extrator "Pacientes (analítico)" — SmartRIS RADIOBRAS
Estratégia híbrida: Playwright (login + tokens) + requests (POST).
"""

import argparse
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://radiobras.smartris.com.br/ris"

# Convênios e segmentos do escopo (nomes exatos como aparecem nos selects)
TARGET_CONVENIOS = [
    "REDE UNNA - CENTRO",
    "REDE UNNA - ITAIGARA",
    "REDE UNNA - PERIPERI",
    "REDE UNNA - LAURO DE FREITAS",
    "REDE UNNA - CAMAÇARI",
    "REDE UNNA CAMINHO DAS ÁRVORES - TANCREDO",
    "REDE UNNA DESCONTO CAMAÇARI",
]
TARGET_SEGMENTOS = ["BRASMED", "CAMAÇARI", "CENTRO", "ITAIGARA", "LAURO", "PERIPERI", "TANCREDO"]


def get_credentials():
    email = os.environ.get("SMARTRIS_EMAIL", "adoni_santos@outlook.com")
    password = os.environ.get("SMARTRIS_PASSWORD", "Andrea@26")
    return email, password


def discover_tokens_and_cookies(email: str, password: str) -> tuple[dict, dict, dict]:
    """
    Usa Playwright para:
    1. Fazer login
    2. Navegar para admin_reports
    3. Selecionar patients_detailed_report
    4. Ler os options de insurance e segments
    Retorna (convenio_map, segmento_map, cookies_dict)
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        print("[1/5] Abrindo página de login...")
        page.goto(f"{BASE_URL}/", wait_until="networkidle")

        print("[2/5] Fazendo login...")
        page.fill('input[name="username"]', email)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(1500)

        # Verificar login
        if "/login" in page.url or "checklogin" in page.url:
            browser.close()
            raise RuntimeError(f"Falha no login — URL pos-submit: {page.url}")
        print(f"   Logado. URL atual: {page.url}")

        print("[3/5] Navegando para admin_reports...")
        page.goto(f"{BASE_URL}/admin_reports", wait_until="networkidle")
        time.sleep(2)

        print("[4/5] Selecionando tipo 'patients_detailed_report'...")
        # O select usa jQuery Chosen (hidden) — usar JS para forçar valor + disparar change
        page.evaluate("""
            (function() {
                var sel = document.querySelector('select[name="r1"]');
                if (!sel) return;
                sel.value = 'patients_detailed_report';
                // Disparar eventos para o Vue/Chosen detectar
                sel.dispatchEvent(new Event('change', {bubbles: true}));
                // Caso seja Chosen plugin
                if (window.$ && $(sel).data('chosen')) {
                    $(sel).trigger('chosen:updated').trigger('change');
                } else if (window.$) {
                    $(sel).trigger('change');
                }
            })()
        """)
        # Aguardar os filtros carregarem (networkidle + tempo extra para Vue renderizar)
        page.wait_for_load_state("networkidle")
        time.sleep(4)

        print("[5/5] Lendo tokens dos selects insurance e segments...")

        # Ler options de convênio
        convenio_map = {}
        insurance_options = page.query_selector_all('select[name="insurance"] option')
        for opt in insurance_options:
            text = opt.inner_text().strip()
            value = opt.get_attribute("value") or ""
            if text and value:
                convenio_map[text] = value
        print(f"   Convênios encontrados: {len(convenio_map)}")

        # Ler options de segmento
        segmento_map = {}
        segment_options = page.query_selector_all('select[name="segments"] option')
        for opt in segment_options:
            text = opt.inner_text().strip()
            value = opt.get_attribute("value") or ""
            if text and value:
                segmento_map[text] = value
        print(f"   Segmentos encontrados: {len(segmento_map)}")

        # Capturar cookies
        pw_cookies = context.cookies()
        cookies_dict = {c["name"]: c["value"] for c in pw_cookies}

        browser.close()
        return convenio_map, segmento_map, cookies_dict


def resolve_tokens(target_names: list[str], token_map: dict, kind: str) -> list[str]:
    tokens = []
    for name in target_names:
        # Busca exata primeiro
        if name in token_map:
            tokens.append(token_map[name])
        else:
            # Busca parcial case-insensitive
            found = [v for k, v in token_map.items() if name.upper() in k.upper()]
            if found:
                tokens.append(found[0])
                print(f"   [AVISO] '{name}' resolvido por match parcial.")
            else:
                print(f"   [ERRO] '{name}' não encontrado nos {kind}s disponíveis.")
                print(f"   Disponíveis: {list(token_map.keys())}")
    return tokens


def post_relatorio(
    cookies: dict,
    insurance_tokens: list[str],
    segment_tokens: list[str],
    date_from: str,
    date_to: str,
) -> str:
    """
    Faz o POST para gerar o relatório e retorna o HTML da resposta.
    """
    url = f"{BASE_URL}/admin_reports/patients_detailed_report/"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"{BASE_URL}/admin_reports",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124",
    }
    payload = {
        "order_by": "",
        "from": date_from,
        "to": date_to,
        "datetype": "realized",
        "insurance": ",".join(insurance_tokens),
        "segments": ",".join(segment_tokens),
        "insurance_exclude": "",
        "global_plan": "",
        "modality": "",
    }

    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value)

    print(f"\n[POST] {url}")
    print(f"   Periodo: {date_from} a {date_to}")
    print(f"   {len(insurance_tokens)} convênio(s), {len(segment_tokens)} segmento(s)")

    for attempt in range(1, 3):
        try:
            resp = session.post(url, headers=headers, data=payload, timeout=60)
            resp.raise_for_status()
            # Verificar se não foi redirecionado para login
            if "checklogin" in resp.url or "login" in resp.url:
                raise RuntimeError("Sessão expirou durante o POST.")
            return resp.text
        except requests.RequestException as e:
            print(f"   Tentativa {attempt} falhou: {e}")
            if attempt == 2:
                raise
            time.sleep(3)


def cell_text(td) -> str:
    """Extrai texto de um TD ignorando spans ocultos (data-sort keys)."""
    for hidden in td.find_all("span", style=lambda s: s and "display:none" in s.replace(" ", "")):
        hidden.decompose()
    return td.get_text(separator=" ", strip=True)


def segmento_from_convenio(convenio: str) -> str:
    """Deriva o segmento a partir do nome do convênio (ex.: 'REDE UNNA - CENTRO / ...' → 'CENTRO')."""
    mapping = {
        "CENTRO": "CENTRO",
        "ITAIGARA": "ITAIGARA",
        "PERIPERI": "PERIPERI",
        "LAURO": "LAURO",
        "CAMAÇARI": "CAMAÇARI",
        "BRASMED": "BRASMED",
        "TANCREDO": "TANCREDO",
        "ÁRVORES": "TANCREDO",
    }
    upper = convenio.upper()
    for key, seg in mapping.items():
        if key in upper:
            return seg
    return ""


def parse_html_to_df(html: str) -> tuple[pd.DataFrame, str, str]:
    """
    Extrai a tabela de dados do HTML da resposta.
    Retorna (DataFrame, valor_total_str, num_exames_str).
    """
    import re

    soup = BeautifulSoup(html, "lxml")

    table = soup.find("table")
    if not table:
        body_text = soup.get_text(separator=" ", strip=True)[:500]
        raise ValueError(f"Nenhuma tabela encontrada na resposta. Trecho: {body_text}")

    # Cabeçalho (9 colunas da resposta real)
    base_headers = []
    thead = table.find("thead")
    if thead:
        base_headers = [cell_text(th) for th in thead.find_all(["th", "td"])]
    if not base_headers:
        first_row = table.find("tr")
        if first_row:
            base_headers = [cell_text(td) for td in first_row.find_all(["th", "td"])]

    # Linhas de dados: manter só as que têm o mesmo nº de colunas que o cabeçalho
    # As linhas de 2 células são subtotais por paciente — descartar.
    expected_cols = len(base_headers) if base_headers else 9
    rows = []
    tbody = table.find("tbody")
    row_source = tbody if tbody else table
    for tr in row_source.find_all("tr"):
        cells = [cell_text(td) for td in tr.find_all(["td", "th"])]
        if len(cells) == expected_cols:
            rows.append(cells)

    if not rows:
        raise ValueError("Tabela encontrada mas sem linhas de dados.")

    # Rodapé
    valor_total = ""
    num_exames = ""
    tfoot = table.find("tfoot")
    footer_text = tfoot.get_text(separator=" ", strip=True) if tfoot else ""
    if not footer_text:
        page_text = soup.get_text(separator="\n")
        for line in page_text.split("\n"):
            if "Valor total" in line or "exames" in line.lower():
                footer_text += line + " "

    m = re.search(r"Valor total[:\s]*R\$\s*([\d.,]+)", footer_text, re.IGNORECASE)
    if m:
        valor_total = "R$ " + m.group(1)
    m2 = re.search(r"N[ºo°]\s*de exames[:\s]*(\d+)", footer_text, re.IGNORECASE)
    if m2:
        num_exames = m2.group(1)

    # Montar DataFrame com as 9 colunas reais
    df = pd.DataFrame(rows, columns=base_headers if base_headers else None)

    # Adicionar coluna "Segmento" derivada de "Convênio" (coluna 4, índice 4)
    convenio_col = "Convênio" if "Convênio" in df.columns else df.columns[4] if len(df.columns) > 4 else None
    if convenio_col:
        df.insert(0, "Segmento", df[convenio_col].apply(segmento_from_convenio))

    return df, valor_total, num_exames


def main():
    parser = argparse.ArgumentParser(description="Extrator Pacientes Analítico — SmartRIS")
    parser.add_argument("--from", dest="date_from", default="28/05/2026", help="Data início DD/MM/YYYY")
    parser.add_argument("--to", dest="date_to", default="28/05/2026", help="Data fim DD/MM/YYYY")
    args = parser.parse_args()

    email, password = get_credentials()

    # Etapa 1: Login + descoberta de tokens
    convenio_map, segmento_map, cookies = discover_tokens_and_cookies(email, password)

    # Etapa 2: Resolver tokens alvo
    insurance_tokens = resolve_tokens(TARGET_CONVENIOS, convenio_map, "convênio")
    segment_tokens = resolve_tokens(TARGET_SEGMENTOS, segmento_map, "segmento")

    if not insurance_tokens:
        print("\n[ERRO] Nenhum token de convênio resolvido. Abortando.")
        sys.exit(1)
    if not segment_tokens:
        print("\n[ERRO] Nenhum token de segmento resolvido. Abortando.")
        sys.exit(1)

    # Etapa 3: POST do relatório
    html = post_relatorio(cookies, insurance_tokens, segment_tokens, args.date_from, args.date_to)

    # Salvar HTML bruto para debug (remover após validação)
    debug_html = os.path.join(os.path.dirname(__file__), "_debug_response.html")
    with open(debug_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"   HTML salvo em: {debug_html}")

    # Etapa 4: Parse
    print("\nParsando HTML da resposta...")
    df, valor_total, num_exames = parse_html_to_df(html)

    print(f"   Linhas extraídas: {len(df)}")
    print(f"   Colunas: {list(df.columns)}")
    print(f"   Valor total (rodapé): {valor_total or '(não encontrado)'}")
    print(f"   Nº de exames (rodapé): {num_exames or '(não encontrado)'}")

    # Validação básica
    if len(df) == 0:
        print("\n[AVISO] Nenhum dado retornado para o período/filtros informados.")

    # Etapa 5: Salvar Excel
    date_tag = args.date_from.replace("/", "") + "_" + args.date_to.replace("/", "")
    output_path = os.path.join(
        os.path.dirname(__file__),
        f"pacientes_analitico_REDEUNNA_{date_tag}.xlsx",
    )
    df.to_excel(output_path, index=False)
    print(f"\n[OK] Arquivo salvo: {output_path}")

    # Resumo de aceitação
    print("\n--- Teste de aceitação ---")
    print(f"Colunas (esperado 10): {df.shape[1]}")
    print(f"Linhas de dados: {len(df)}")
    print(f"Valor total (rodapé): {valor_total}")
    print(f"Nº exames (rodapé):   {num_exames}")
    if num_exames.isdigit():
        print(f"Linhas == Nº exames: {len(df) == int(num_exames)}")


if __name__ == "__main__":
    main()
