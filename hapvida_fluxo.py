"""
hapvida_fluxo.py — Orquestrador do faturamento Hapvida +Odonto de UM dia/unidade.

  1. lista do dia: Procedimentos Executados (portal)
  2. guia já tem imagem? pula              (portal: Banco de Imagens)
  3. nome + nascimento do paciente         (portal: situação do usuário)
  4. entregável + pedido                   (fonte PRORADIS)
  5. decisão determinística                (hapvida_decisao)
  6. anexar + CONFIRMAR relendo o banco    (portal) — só com envio real liberado

As dependências entram por parâmetro (portal, fonte) para o fluxo ser testável
sem rede. O resumo sai no mesmo formato que db.salvar_execucao já grava.

SEGURANÇA: dry_run=True por padrão. O envio real exige dry_run=False E a variável
HAPVIDA_ANEXAR_REAL=1 — trava dupla (o dono dá o GO, como no RedeUna).
"""
from __future__ import annotations

import os
import tempfile
import time

import hapvida_decisao as hd

PLANO = "hapvida_odonto"


def conta_hapvida(unidade: str) -> str:
    """Chave da unidade nas tabelas compartilhadas. O prefixo separa do OdontoPrev."""
    return f"hapvida:{unidade.strip().lower()}"


def envio_real_liberado(dry_run: bool) -> bool:
    return (not dry_run) and os.environ.get("HAPVIDA_ANEXAR_REAL") == "1"


def rodar_hapvida(dia: str, unidade: str, *, portal, fonte, dry_run: bool = True,
                  log=None, apenas_guias=None) -> dict:
    log = log or (lambda m: print(m, flush=True))
    t0 = time.monotonic()
    real = envio_real_liberado(dry_run)
    conta = conta_hapvida(unidade)
    log(f"[hapvida] {dia} {conta} — {'REAL' if real else 'SIMULAÇÃO'}")

    linhas = portal.listar_executados(dia)
    guias = hd.agrupar_por_guia(linhas)
    if apenas_guias:
        alvo = set(map(str, apenas_guias))
        guias = {g: v for g, v in guias.items() if g in alvo}
    log(f"[hapvida] {len(linhas)} itens em {len(guias)} guias")

    decisoes, ok = [], 0
    tmp = tempfile.mkdtemp(prefix="_hap_")
    for num, g in guias.items():
        item = {"gto": num, "paciente": g["nome"],
                "gto_exames": [hd.CODIGOS.get(i["codigo"], i["codigo"]) for i in g["itens"]],
                "anexado": None, "categoria": None, "gemini": {}}
        try:
            ja = portal.guia_tem_imagem(g["usuario"], num, dia)
            pac = None if ja else portal.paciente(g["usuario"])
            pr = None if ja else fonte.buscar(nome=(pac or {}).get("nome") or g["nome"],
                                              nascimento=(pac or {}).get("nascimento"),
                                              dia=dia, codigos=[i["codigo"] for i in g["itens"]], gto=num)
            dec = hd.decidir_guia(g, ja, pac, pr)
            if pac:
                item["paciente"] = pac.get("nome") or g["nome"]
            if (pr or {}).get("data_exame_real"):
                # exame achado em outro dia da janela: fica registrado, como no RedeUna
                item["data_exame_real"] = pr["data_exame_real"]
            item["categoria"] = dec.categoria
            item["gemini"] = {"motivo": dec.motivo, "responsavel": dec.responsavel}

            if dec.faturavel:
                arquivos = [hd.adequar_arquivo(a, tmp) for a in dec.anexar]
                item["arquivos_anexados"] = [os.path.basename(a) for a in arquivos]
                item["laudo_imgs"] = item["arquivos_anexados"]
                if real:
                    portal.anexar(g["usuario"], num, arquivos, itens=len(g["itens"]))
                    if portal.guia_tem_imagem(g["usuario"], num, dia):
                        item["anexado"] = "OK"
                        ok += 1
                    else:
                        item["categoria"] = "revisao"
                        item["anexar_erro"] = "enviado, mas a imagem não apareceu no Banco de Imagens"
                else:
                    item["anexado"] = "SIMULADO"
                log(f"[hapvida] {num} {item['paciente']}: anexaria {item['arquivos_anexados']}")
            elif dec.categoria == hd.JA_ANEXADA:
                # "OK" como na esteira: db.salvar_execucao marca faturado e NÃO abre
                # pendência. A categoria ja_anexada separa do anexo feito por nós.
                item["anexado"] = "OK"
            else:
                log(f"[hapvida] {num} {item['paciente']}: {dec.categoria} — {dec.motivo}")
        except Exception as e:  # falha nossa: registra e segue para a próxima guia
            item["categoria"] = "erro"
            item["erro"] = f"falha técnica: {str(e)[:200]}"
            item["gemini"] = {"motivo": item["erro"], "responsavel": "Nós"}
            log(f"[hapvida] {num}: ERRO {e}")
        decisoes.append(item)

    return {
        "plano": PLANO, "data": dia, "conta": conta, "dry_run": not real,
        "decisoes": decisoes, "baixados": len(decisoes), "anexado_ok": ok,
        "faturaveis": sum(1 for d in decisoes if d["anexado"] in ("OK", "SIMULADO")
                          and d["categoria"] != hd.JA_ANEXADA),
        "pendentes": sum(1 for d in decisoes if d["anexado"] not in ("OK", "SIMULADO")),
        "tempo_total": int(time.monotonic() - t0),
    }
