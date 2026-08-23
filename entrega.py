"""Módulo ENTREGA do PRORADIS — onde vive o exame de MODELO.

**Por que este arquivo existe.** O exame de MODELO (procedimento `81000308`) **não
aparece na worklist de laudos** (`reports_list`), que era o único lugar onde a
esteira procurava entregável. Medido em 22/08: a worklist de 13/08 traz 93
accessions, de `40342100` a `40342210`; o modelo do HANIEL é `40342410`. Busca por
nome numa janela de 22 dias não devolve linha nenhuma. Resultado prático: a guia
caía em "exame existe mas não há laudo nem imagem" e a mensagem mandava **cobrar o
laudo do radiologista** — de um exame que, por definição, não tem laudo.

Ele vive no módulo **Entrega** (`/delivery/get_list`), com uma pegadinha que
escondeu isso por semanas: a tela manda `filtro[origens]=EXTERNO` por padrão, e com
esse valor o modelo **não aparece**. Só com `origens` vazio.

O entregável sai de `delivery/print_series` com o `study_id`: o render 3D em 5/6
vistas — frontal em oclusão, laterais, oclusal superior e inferior.

Ele vem em duas formas, e **a A4 nem sempre existe** (medido nos 3 casos de agosto):
uma folha A4 com logo RadioBras e cabeçalho do paciente (HANIEL, 13/08) e uma grade
crua sem cabeçalho (RICARDO, 19/08). A LUIZA (20/08) não tinha nenhuma — render não
gerado ainda, que é pendência legítima. A regra de escolha está em
`escolher_entregaveis`; ela é a única exceção à trava da logo verde.
"""
import re
import urllib.parse

CAMINHO_LISTA = "/delivery/get_list"
CAMINHO_IMPRESSAO = "/delivery/print_series"

# Nome do procedimento que identifica a guia de MODELO puro.
_MODELO_RE = re.compile(r"^\s*modelo\b", re.I)
# ...mas 'DOCUMENTACAO COMPLETA C/MODELO' é outra coisa: um PACOTE (imagens +
# laudos + o modelo) que TEM laudo. Confundir os dois faria o robô parar de exigir
# laudo das 157 guias de documentação de agosto — o oposto do que se quer.
_DOC_RE = re.compile(r"documenta", re.I)


def eh_modelo(exame) -> bool:
    """É a guia de MODELO puro (o exame começa com 'MODELO')?"""
    t = str(exame or "")
    if _DOC_RE.search(t):
        return False
    return bool(_MODELO_RE.search(t))


def montar_body(de: str, ate: str, nome: str = "", exame: str = "") -> str:
    """Corpo do POST de `/delivery/get_list`.

    `filtro[origens]` vai VAZIO de propósito — com 'EXTERNO' (o padrão da tela) o
    exame de modelo não aparece. Foi essa linha que escondeu o problema."""
    pares = {
        "filtro[exames]": "todos",
        "filtro[origens]": "",          # <- a pegadinha; não preencher
        "filtro[segmento]": "",
        "optionsRadios": "on",
        "filtro[pat_id]": "",
        "filtro[nome]": nome or "",
        "filtro[exam]": exame or "",
        "filtro[dtnascimento]": "",
        "filtro[pedido]": "",
        "filtro[solicitante]": "",
        "filtro[comentario]": "",
        "filtro[verificador]": "",
        "filtro[validador]": "",
        "filtro[origin]": "",
        "filtro_data_inicio": de,
        "filtro_data_fim": ate + " 23:59:59" if " " not in ate else ate,
    }
    return urllib.parse.urlencode(pares)


def parse_linhas(html) -> list:
    """Lê as linhas da lista de Entrega.

    O `study_id` sai do `value` do checkbox `deliver-check` — é o mesmo hash que o
    `print_series` consome, e é mais estável do que parsear o `onclick`."""
    from bs4 import BeautifulSoup
    if not html:
        return []
    soup = BeautifulSoup(str(html), "lxml")
    out = []
    for tr in soup.find_all("tr"):
        chk = tr.find("input", attrs={"data-accession-no": True})
        if not chk:
            continue
        study = (chk.get("value") or "").strip()
        if not study:
            continue
        tds = [re.sub(r"\s+", " ", t.get_text(" ", strip=True))
               for t in tr.find_all("td")]

        def _td(i):
            return tds[i].strip() if len(tds) > i else ""
        quando = _td(4)
        out.append({
            "accession": (chk.get("data-accession-no") or "").strip(),
            "study_id": study,
            "status": _td(2),
            "dia": quando.split(" ")[0] if quando else "",
            "hora": quando.split(" ")[1] if " " in quando else "",
            "exame": _td(6),
            "paciente": _td(7),
            "solicitante": _td(8),
            "entrega": _td(10),
            "nascimento": _td(11),
            "unidade": _td(13),
        })
    return out


# ── JS que roda DENTRO da página logada ─────────────────────────────────────
# Igual ao `reports_doc`: por `requests` estas chamadas voltam a tela de login.
JS_LISTA = """async ([base, body]) => {
    const r = await fetch(base + '/delivery/get_list', {
        method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: body});
    return await r.text();
}"""

JS_IMPRIMIR = """([base, study]) => {
    const f = document.createElement('form');
    f.method = 'POST';
    f.action = base + '/delivery/print_series';
    const i = document.createElement('input');
    i.name = 'study_id'; i.value = study;
    f.appendChild(i);
    document.body.appendChild(f);
    f.submit();
}"""


def buscar(page, base: str, de: str, ate: str, nome: str = "") -> list:
    """Linhas da Entrega no período. Falha quieto: lista vazia, nunca levanta —
    isto é um CAMINHO ALTERNATIVO; se ele cair, o fluxo normal segue igual."""
    try:
        html = page.evaluate(JS_LISTA, [base, montar_body(de, ate, nome)])
    except Exception:
        return []
    return parse_linhas(html)


def achar_modelo(page, base: str, dia: str, paciente: str = "",
                 accession: str = "") -> dict:
    """A linha do exame de MODELO do paciente/dia. None se não houver.

    Casa por accession quando ele é conhecido (chave forte); senão, por paciente."""
    for li in buscar(page, base, dia, dia, nome=(paciente or "")[:20]):
        if not eh_modelo(li.get("exame")):
            continue
        if accession and li.get("accession") != str(accession):
            continue
        if paciente and not accession:
            a = re.sub(r"\s+", " ", str(paciente)).strip().upper()
            b = re.sub(r"\s+", " ", li.get("paciente") or "").strip().upper()
            if a and b and a not in b and b not in a:
                continue
        return li
    return None


def escolher_entregaveis(itens) -> list:
    """Quais imagens do MODELO anexar. `itens` = [{"bytes":..., "logo": bool}].

    **A folha A4 quando ela existe; senão, todas as cruas.**

    A regra nasceu de medição, não de teoria: nos 3 casos de agosto a A4 com
    logo+cabeçalho existia em 1 de 3 (HANIEL 13/08). O RICARDO (19/08) só tinha a
    grade crua 708x818 — com as MESMAS 6 vistas — e a LUIZA (20/08) não tinha nada
    ainda. Travar na A4 deixaria o RICARDO parado esperando alguém gerar a folha à
    mão, que é justamente o trabalho humano que se quer eliminar.

    É a ÚNICA exceção à trava "só anexa o que tem a logo verde", e ela é estreita
    de propósito: vale só para estudo que já sabemos ser MODELO, alcançado por
    ACCESSION no módulo Entrega. A imagem crua não traz o nome do paciente, mas ela
    não chega solta — chega amarrada a um accession que nós resolvemos, dentro de
    uma guia que nomeia o paciente.

    Nunca "a maior": escolher uma vista sozinha entregaria menos ângulos do que o
    exame tem. Ou a A4, ou o conjunto inteiro."""
    itens = [i for i in (itens or []) if i]
    com_logo = [i for i in itens if i.get("logo")]
    return com_logo if com_logo else itens
