"""
hapvida_app.py — Liga o fluxo Hapvida ao app: rodada de UM dia/unidade (tela) e a
rodada automática (cron). O app.py só chama estas funções.

MODO SOMBRA: sem HAPVIDA_ANEXAR_REAL=1 tudo roda em SIMULAÇÃO — grava a execução
(dry_run) no histórico para conferência, não abre pendência e não anexa nada.

Cron (HAPVIDA_CRON=1, dentro do mesmo horário do OdontoPrev):
  - dias = os HAPVIDA_CRON_DIAS (3) dias anteriores a hoje + os dias Hapvida com
    pendência aberta no prazo. Sem D-4: o Hapvida não depende de relatório da
    operadora, e a norma manda anexar no dia (a clínica anexa 1-2 dias depois);
  - unidades = HAPVIDA_CONTAS;
  - um navegador do PRORADIS para a rodada inteira (o analítico é 1 por dia).
"""
from __future__ import annotations

import datetime as _dt
import os

import hapvida_fluxo as hf


def unidades() -> list[str]:
    from extrator_hapvida import listar_contas_hapvida
    return listar_contas_hapvida()


def dias_do_cron(hoje: _dt.date, pendentes=(), n: int | None = None) -> list[str]:
    """Os n dias antes de hoje (D-1..D-n) + dias com pendência. 'DD/MM/AAAA', sem repetir."""
    if n is None:
        try:
            n = int(os.environ.get("HAPVIDA_CRON_DIAS", "3"))
        except ValueError:
            n = 3
    dias = {(hoje - _dt.timedelta(days=i)).strftime("%d/%m/%Y") for i in range(1, n + 1)}
    dias |= {d for d in pendentes if d}
    return sorted(dias, key=lambda d: _dt.datetime.strptime(d, "%d/%m/%Y"))


def rodar_dia(dia: str, unidade: str, *, fonte=None, dry_run: bool = False, log=None,
              apenas_guias=None) -> dict:
    """Uma unidade num dia. `fonte` aberta pode ser reaproveitada (cron)."""
    from hapvida_portal import PortalHapvida
    portal = PortalHapvida(unidade)
    if fonte is not None:
        return hf.rodar_hapvida(dia, unidade, portal=portal, fonte=fonte, dry_run=dry_run,
                                log=log, apenas_guias=apenas_guias)
    from hapvida_proradis import FonteProradis
    with FonteProradis(log=log) as f:
        return hf.rodar_hapvida(dia, unidade, portal=portal, fonte=f, dry_run=dry_run,
                                log=log, apenas_guias=apenas_guias)


def rodada_cron(hoje: _dt.date, *, reservar, liberar, salvar, salvar_falha, logger) -> dict:
    """Rodada automática. reservar/liberar = trava (dia, conta) do app; salvar/
    salvar_falha = db.salvar_execucao / db.salvar_execucao_falha."""
    import db
    try:
        prazo = int(os.environ.get("FATURAR_PRAZO_DIAS", "7"))
    except ValueError:
        prazo = 7
    pend = {}
    for conta, dia in db.dias_com_pendencia_aberta(prazo, hapvida=True):
        pend.setdefault(conta, set()).add(dia)
    feitos = fat = 0
    from hapvida_proradis import FonteProradis
    with FonteProradis(log=lambda m: None) as fonte:
        for u in unidades():
            conta = hf.conta_hapvida(u)
            for dia in dias_do_cron(hoje, pend.get(conta, ())):
                tag = f"cron-{conta}-{dia}"
                if not reservar(dia, conta, tag):
                    logger.warning("Cron hapvida %s %s PULADO: já há execução", conta, dia)
                    continue
                logs = []
                try:
                    r = rodar_dia(dia, u, fonte=fonte, dry_run=False, log=logs.append)
                    try:
                        salvar(r, logs)
                    except Exception as e:
                        logger.error("Cron hapvida %s %s: falhou ao gravar: %s", conta, dia, str(e)[:120])
                    feitos += 1
                    fat += r.get("anexado_ok", 0) or 0
                    logger.info("Cron hapvida %s %s: fat=%s pend=%s sim=%s", conta, dia,
                                r.get("anexado_ok"), r.get("pendentes"), r.get("dry_run"))
                except Exception as e:
                    logger.error("Cron hapvida %s %s FALHOU: %s", conta, dia, str(e)[:120])
                    try:
                        salvar_falha(dia, conta, not hf.envio_real_liberado(False), str(e), logs)
                    except Exception:
                        pass
                finally:
                    liberar(dia, conta, tag)
    return {"execucoes": feitos, "faturadas": fat}
