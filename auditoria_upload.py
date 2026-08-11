"""
auditoria_upload.py — Camada 2 do QA de faturamento.

Confere, guia por guia, se os documentos que O ROBÔ registrou ter anexado estão
REALMENTE na guia do OdontoPrev. Pega a falha SILENCIOSA: a anexação reportou
"OK", mas o arquivo não persistiu (JWT expirado no meio do run, colisão de nome,
portal que aceitou e descartou) — o tipo de coisa que vira glosa "documentação
ausente" e que, sem esta conferência, ninguém enxerga até a operadora glosar.

Duas partes:
  - auditar_guia() / _tipos_nossos()  -> veredito PURO e testável
    (test_auditoria.py). Não abre navegador, não toca no banco.
  - auditar_dia()                     -> driver SÓ-LEITURA do portal. Puxa do
    banco as guias que faturamos no dia, abre cada uma no OdontoPrev e confere.
    NÃO anexa, NÃO altera nada — apenas lê os anexos.

Nomes deterministas da automação (prova de autoria), iguais aos usados em todo o
pipeline (ver _ja_anexado_por_nos em fechar_dia.py e _chave_anexo em
extrator_odontoprev.py): LAUDO_*, ENTREGA_*, SOLICITACAO_*. Anexo manual usa
outro nome e NÃO casa aqui — por isso "AUSENTE" significa mesmo "não subiu",
não "subiu com outro nome".
"""


def _tipo_nosso(nome: str):
    """Tipo do arquivo NOSSO ('laudo' | 'img' | 'solic'), ou None se não é nosso.

    Presença é LENIENTE (startswith): um falso "AUSENTE" alarma à toa e mina a
    confiança na auditoria, então só marcamos ausência quando NADA nosso casa.
    """
    u = str(nome or "").strip().upper()
    if u.startswith("LAUDO_"):
        return "laudo"
    if u.startswith("ENTREGA_"):
        return "img"
    if u.startswith("SOLICITACAO_"):
        return "solic"
    return None


def _tipos_nossos(nomes) -> set:
    """Conjunto de tipos de arquivo nosso presentes numa lista de nomes."""
    return {t for t in (_tipo_nosso(n) for n in (nomes or [])) if t}


def _laudos_faltando(esperados, portal) -> list:
    """LAUDOS especificos (por IDENTIDADE acc+TIPO) que dissemos ter anexado mas
    NAO estao na guia. Fecha o ponto cego do check por TIPO: uma guia com 2 exames
    espera 2 laudos; se so 1 grudou, o tipo 'laudo' esta presente e o check grosso
    dava OK — deixando passar a guia sem um dos laudos (NILSON/RENATA). Compara por
    _chave_anexo (ignora o rotulo do exame), entao rotulo trocado nao vira falso
    faltante. Retorna os NOMES esperados cujo laudo nao casou nenhum do portal."""
    from extrator_odontoprev import _chave_anexo  # lazy: mantem auditar_guia puro
    chaves_portal = {_chave_anexo(n) for n in (portal or [])}
    faltam = []
    for n in (esperados or []):
        if str(n or "").strip().upper().startswith("LAUDO_"):
            if _chave_anexo(n) not in chaves_portal:
                faltam.append(str(n).strip())
    return sorted(set(faltam))


def auditar_guia(esperados, portal) -> dict:
    """Veredito de UMA guia que o robô diz ter faturado.

    esperados: nomes que o robô REGISTROU ter anexado (banco: arquivos_plano +
        solicitacao). É a nossa afirmação — o que deveria estar na guia.
    portal:    nomes dos anexos que ESTÃO na guia agora (lidos do OdontoPrev).

    Retorna {status, faltando, laudos_faltando, esperava, no_portal}:
      OK        — tudo que dissemos ter anexado está lá.
      PARCIAL   — parte chegou, parte sumiu (ex.: subiu a solicitação, o laudo não;
                  ou 2 laudos esperados e só 1 grudou — ver laudos_faltando).
      AUSENTE   — dissemos ter anexado, mas NADA nosso está na guia (falha silenciosa).
      SEM_PLANO — o banco não registrou o que anexamos E não há arquivo nosso na
                  guia: não dá para afirmar; revisar à mão (execução antiga).

    faltando: TIPOS (laudo/img/solic) ausentes — visão grossa, compatível.
    laudos_faltando: LAUDOS específicos (por acc+TIPO) que sumiram — visão fina,
        pega o laudo que faltou mesmo com o tipo 'laudo' presente.
    """
    esperava = _tipos_nossos(esperados)
    no_portal = _tipos_nossos(portal)
    laudos_faltando = _laudos_faltando(esperados, portal)
    if not esperava:
        # Banco não diz o que anexamos. Se há QUALQUER arquivo nosso na guia, o
        # upload aconteceu (OK); se não, não temos como afirmar (SEM_PLANO).
        status = "OK" if no_portal else "SEM_PLANO"
        return {"status": status, "faltando": [], "laudos_faltando": laudos_faltando,
                "esperava": [], "no_portal": sorted(no_portal)}
    faltando = sorted(esperava - no_portal)
    if not faltando:
        status = "OK"
    elif esperava & no_portal:
        status = "PARCIAL"
    else:
        status = "AUSENTE"
    # visão fina: um laudo específico que faltou rebaixa OK -> PARCIAL, mesmo que o
    # tipo 'laudo' esteja presente por causa de OUTRO laudo da mesma guia.
    if laudos_faltando and status == "OK":
        status = "PARCIAL"
    return {"status": status, "faltando": faltando, "laudos_faltando": laudos_faltando,
            "esperava": sorted(esperava), "no_portal": sorted(no_portal)}


def _faturadas_do_dia(dia: str, contas=None) -> list:
    """Guias que O ROBÔ faturou (faturado=True) num dia, com o que ele registrou
    ter anexado. Só execuções REAIS (dry não conta); a mais recente por GTO vence.

    Retorna [{conta, gto, paciente, esperados[]}].
    """
    import db
    with db.SessionLocal() as s:
        q = (s.query(db.Execucao)
             .filter(db.Execucao.dia == dia, db.Execucao.dry_run == False)  # noqa: E712
             .order_by(db.Execucao.criado_em.asc()))
        execs = [e for e in q.all() if not contas or e.conta in contas]
        por_gto = {}
        for e in execs:
            for it in e.itens:
                if not it.faturado:
                    continue
                nomes = []
                if it.arquivos_plano:
                    nomes += [x.strip() for x in it.arquivos_plano.split(",") if x.strip()]
                if it.solicitacao:
                    nomes.append(str(it.solicitacao).strip())
                por_gto[str(it.gto)] = {"conta": e.conta, "gto": str(it.gto),
                                        "paciente": it.paciente, "esperados": nomes}
    return list(por_gto.values())


def _ler_anexos_estavel(gp, tentativas=10, intervalo_ms=600):
    """Le os anexos de uma guia esperando a LISTA renderizar. Uma guia faturada tem
    SEMPRE ao menos a copia da GTO (img_ASSINADA); uma leitura VAZIA e quase sempre a
    lista ainda carregando, nao ausencia real. Re-le ate duas leituras consecutivas
    baterem e nao-vazias (estavel) — evita o FALSO AUSENTE (caso MARCOS BOJCO/
    195763490: OK numa rodada, 'portal=[]' na seguinte, minutos depois)."""
    from extrator_odontoprev import _anexos_nomes
    anterior = set()
    for _ in range(tentativas):
        nomes = set(_anexos_nomes(gp))
        if nomes and nomes == anterior:
            return nomes
        anterior = nomes
        try:
            gp.wait_for_timeout(intervalo_ms)
        except Exception:
            break
    return anterior


def auditar_dia(dia: str, contas=None, log=print) -> list:
    """Driver SÓ-LEITURA: abre no OdontoPrev cada guia que faturamos no dia e
    confere se nossos arquivos estão lá. NÃO anexa nada.

    Retorna a lista de {conta, gto, paciente, status, faltando, no_portal,
    portal_nomes, esperados}. Uma falha de login/abertura vira status próprio
    (ERRO_LOGIN / ERRO_ABRIR) — nunca derruba a auditoria inteira.
    """
    from playwright.sync_api import sync_playwright
    import db
    from extrator_odontoprev import (login_odonto, abrir_consultar_gtos,
                                     consultar_periodo, abrir_gto, _anexos_nomes)

    alvos = _faturadas_do_dia(dia, contas)
    por_conta = {}
    for a in alvos:
        por_conta.setdefault(a["conta"], []).append(a)
    if not alvos:
        log(f"[auditoria] nenhuma guia faturada por nós em {dia} (contas={contas})")
        return []

    resultados = []
    with sync_playwright() as pw:
        for conta, guias in por_conta.items():
            log(f"[auditoria] {conta}: {len(guias)} guia(s) faturada(s) a conferir")
            senha = db.get_portal_senha(conta)
            try:
                b, _c, page = login_odonto(pw, conta, senha)
            except Exception as e:
                for g in guias:
                    resultados.append({**g, "status": "ERRO_LOGIN",
                                       "faltando": [], "no_portal": [],
                                       "portal_nomes": [], "detalhe": str(e)[:100]})
                continue
            try:
                abrir_consultar_gtos(page)
                consultar_periodo(page, dia)
            except Exception as e:
                # Falha de MENU/PERIODO é da sessão desta conta, não das guias.
                # Marca as guias desta conta para reauditar e SEGUE para a próxima
                # — antes, uma conta com menu instável (Tancredo) abortava o dia
                # inteiro e derrubava até as guias já conferidas de outra unidade.
                log(f"  [ERRO_MENU] {conta}: {str(e)[:70]} — guias marcadas p/ reauditar")
                for g in guias:
                    resultados.append({**g, "status": "ERRO_ABRIR",
                                       "faltando": [], "no_portal": [],
                                       "portal_nomes": [], "detalhe": ("menu: " + str(e))[:100]})
                try:
                    b.close()
                except Exception:
                    pass
                continue
            try:
                for g in guias:
                    try:
                        gp = abrir_gto(page, g["gto"])
                        nomes = sorted(_ler_anexos_estavel(gp))
                        try:
                            gp.close()
                        except Exception:
                            pass
                    except Exception as e:
                        # tenta reconsultar o período uma vez (a tabela pode ter
                        # sido reciclada pelo Vuetify) antes de desistir da guia
                        try:
                            consultar_periodo(page, dia)
                            gp = abrir_gto(page, g["gto"])
                            nomes = sorted(_ler_anexos_estavel(gp))
                            try:
                                gp.close()
                            except Exception:
                                pass
                        except Exception as e2:
                            resultados.append({**g, "status": "ERRO_ABRIR",
                                               "faltando": [], "no_portal": [],
                                               "portal_nomes": [], "detalhe": str(e2)[:80]})
                            continue
                    v = auditar_guia(g["esperados"], nomes)
                    if v["status"] in ("AUSENTE", "PARCIAL"):
                        # AUSENTE/PARCIAL manda a operacao re-anexar — nao pode ser
                        # falso. Leitura vazia/parcial costuma ser a lista ainda
                        # renderizando (caso MARCOS BOJCO). Reabre, re-le estavel e
                        # fica com a MELHOR leitura; so mantem o alarme se persistir.
                        try:
                            gp2 = abrir_gto(page, g["gto"])
                            nomes2 = sorted(_ler_anexos_estavel(gp2))
                            try:
                                gp2.close()
                            except Exception:
                                pass
                            v2 = auditar_guia(g["esperados"], nomes2)
                            if len(v2["no_portal"]) >= len(v["no_portal"]):
                                v, nomes = v2, nomes2
                        except Exception:
                            pass
                    item = {**g, **v, "portal_nomes": nomes}
                    marca = {"OK": "  ok", "PARCIAL": "!!PARCIAL", "AUSENTE": "!!!AUSENTE",
                             "SEM_PLANO": "  s/plano"}.get(v["status"], v["status"])
                    log(f"  [{marca}] GTO {g['gto']} {(g.get('paciente') or '')[:28]:28} "
                        f"esperava={v['esperava']} portal={v['no_portal']} "
                        f"faltando={v['faltando']}"
                        + (f" LAUDO_FALTANDO={v['laudos_faltando']}"
                           if v.get('laudos_faltando') else ""))
                    resultados.append(item)
            finally:
                try:
                    b.close()
                except Exception:
                    pass
    return resultados


def resumir(resultados: list) -> dict:
    """Conta os vereditos por status. AUSENTE/PARCIAL = agir; o resto é confirmação."""
    por = {}
    for r in resultados:
        por[r["status"]] = por.get(r["status"], 0) + 1
    return {
        "total": len(resultados),
        "ok": por.get("OK", 0),
        "parcial": por.get("PARCIAL", 0),
        "ausente": por.get("AUSENTE", 0),
        "sem_plano": por.get("SEM_PLANO", 0),
        "erro_login": por.get("ERRO_LOGIN", 0),
        "erro_abrir": por.get("ERRO_ABRIR", 0),
        "acao": [r for r in resultados if r["status"] in ("AUSENTE", "PARCIAL")],
    }
