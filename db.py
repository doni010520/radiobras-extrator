"""
db.py — Persistência das execuções (histórico p/ dashboard).

Usa Postgres (Supabase) em produção via DATABASE_URL; cai em SQLite local
(radiobras.db) quando a variável não está definida — assim o dev local roda
sem precisar de banco externo.

Tabelas:
  runs      — uma linha por execução de "Fechar o dia" (resumo + métricas).
  run_itens — uma linha por GTO daquela execução (p/ funil e fila de revisão).
"""
import json
import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine, func,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

# Carrega .env local (no Render/EasyPanel as vars já vêm do ambiente).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///radiobras.db")
# Supabase/Heroku às vezes entregam 'postgres://'; SQLAlchemy quer 'postgresql://'.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


def _now():
    return datetime.now(timezone.utc)


class Run(Base):
    __tablename__ = "runs"
    id = Column(Integer, primary_key=True)
    plano = Column(String(40), index=True, default="odontoprev")  # slug do plano
    dia = Column(String(10), index=True)            # DD/MM/AAAA processado
    dry_run = Column(Boolean, default=False)
    status = Column(String(20), default="running")  # running | done | error
    started_at = Column(DateTime(timezone=True), default=_now)
    finished_at = Column(DateTime(timezone=True))
    # métricas (resumo)
    alvos = Column(Integer, default=0)
    enviados = Column(Integer, default=0)
    prontos = Column(Integer, default=0)
    erros = Column(Integer, default=0)
    sem_match = Column(Integer, default=0)
    sem_laudo = Column(Integer, default=0)
    sem_imagens = Column(Integer, default=0)
    revisao_humana = Column(Integer, default=0)
    solic_anexada = Column(Integer, default=0)
    erro_msg = Column(Text)
    log = Column(Text)            # log de progresso completo da execução
    itens = relationship("RunItem", back_populates="run", cascade="all, delete-orphan")


class RunItem(Base):
    __tablename__ = "run_itens"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    gto = Column(String(30))
    paciente = Column(String(200))
    status = Column(String(30))            # ENVIADO | ERRO_UPLOAD | SEM_MATCH | ...
    justificativa = Column(String(30))     # PREENCHIDA | VAZIA | ...
    enviados = Column(Integer, default=0)
    ja_anexados = Column(Integer, default=0)
    solicitacao = Column(String(200))
    revisao_humana = Column(Text)
    detalhe = Column(Text)
    run = relationship("Run", back_populates="itens")


class GlosaEvento(Base):
    """Um evento de glosa lido do Relatório de Glosa (por unidade/período).
    Cada extração grava um 'lote' (carimbo) — o panorama usa o lote mais recente."""
    __tablename__ = "glosa_eventos"
    id = Column(Integer, primary_key=True)
    lote = Column(String(20), index=True)          # YYYYMMDDHHMMSS da extração
    captured_at = Column(DateTime(timezone=True), default=_now)
    dia = Column(String(10))                        # data-fim do período (DD/MM/AAAA)
    conta = Column(String(20), index=True)          # código da unidade
    unidade = Column(String(60), index=True)        # rótulo da unidade
    ficha = Column(String(30), index=True)          # nº da guia/GTO
    paciente = Column(String(200))
    evento_cod = Column(String(20))
    evento = Column(String(200))                    # procedimento
    glosa_cod = Column(String(10), index=True)
    glosa_motivo = Column(String(200))
    recurso_estado = Column(String(20))             # RECURSAVEL | SEM_GLOSADO | ...
    situacao = Column(String(30), index=True)       # A_RECORRER | NAO_RECURSAVEL | ...
    demo_glosado = Column(String(20))               # valor glosado no Demonstrativo (R$)
    demo_pago = Column(Boolean, default=False)       # houve pagamento no Demonstrativo?


class AnexacaoGto(Base):
    """Estado de anexação/faturamento de uma GTO (varredura só-leitura por unidade)."""
    __tablename__ = "anexacao_gtos"
    id = Column(Integer, primary_key=True)
    lote = Column(String(20), index=True)
    captured_at = Column(DateTime(timezone=True), default=_now)
    de = Column(String(10))
    ate = Column(String(10))
    conta = Column(String(20), index=True)
    unidade = Column(String(60), index=True)
    gto = Column(String(30), index=True)
    paciente = Column(String(200))
    liberacao = Column(String(10))                  # data de liberação da senha (DD/MM/AAAA)
    status = Column(String(80))
    qtd_anexos = Column(Integer, default=0)
    categoria = Column(String(20), index=True)


class Execucao(Base):
    """Uma execução do pipeline novo (descoberta->download->decisão->anexação)."""
    __tablename__ = "execucoes"
    id = Column(Integer, primary_key=True)
    dia = Column(String(10), index=True)             # DD/MM/AAAA processado
    criado_em = Column(DateTime(timezone=True), default=_now, index=True)
    dry_run = Column(Boolean, default=False)
    tempo_total = Column(Integer, default=0)          # segundos
    tempo_descoberta = Column(Integer, default=0)
    tempo_download = Column(Integer, default=0)
    pendentes = Column(Integer, default=0)
    faturadas = Column(Integer, default=0)            # anexadas OK
    nao_faturadas = Column(Integer, default=0)
    m_download = Column(Integer, default=0)
    k_leitura = Column(Integer, default=0)
    itens = relationship("ExecucaoItem", back_populates="execucao",
                         cascade="all, delete-orphan")


class ExecucaoItem(Base):
    __tablename__ = "execucao_itens"
    id = Column(Integer, primary_key=True)
    execucao_id = Column(Integer, ForeignKey("execucoes.id", ondelete="CASCADE"), index=True)
    gto = Column(String(30))
    paciente = Column(String(200))
    categoria = Column(String(20))          # auto | justificativa | sem_solicitacao | revisao
    faturado = Column(Boolean, default=False)
    motivo = Column(Text)                   # por que NÃO faturou (quando aplicável)
    solicitacao = Column(String(250))       # arquivo anexado (quando AUTO)
    exames_gto = Column(Text)
    exames_lidos = Column(Text)
    n_arquivos = Column(Integer, default=0)
    execucao = relationship("Execucao", back_populates="itens")


def salvar_execucao(resumo: dict) -> int:
    """Persiste uma execução (resumo do rodar_esteira) + seus itens."""
    with SessionLocal() as s:
        baix = resumo.get("baixados", 0)
        ex = Execucao(
            dia=resumo.get("data"), dry_run=bool(resumo.get("dry_run", True)),
            tempo_total=int(resumo.get("tempo_total", 0)),
            tempo_descoberta=int(resumo.get("tempo_descoberta", 0)),
            tempo_download=int(resumo.get("tempo_ate_download", 0)),
            pendentes=int(resumo.get("pendentes", 0)),
            faturadas=int(resumo.get("anexado_ok", 0)),
            nao_faturadas=int(baix - resumo.get("anexado_ok", 0)),
            m_download=int(resumo.get("m_download", 0) or 0),
            k_leitura=int(resumo.get("k_leitura", 0) or 0),
        )
        for x in resumo.get("decisoes", []):
            g = x.get("gemini") or {}
            faturado = x.get("anexado") == "OK"
            cat = x.get("categoria")
            if faturado:
                motivo = ""
            elif cat == "sem_solicitacao":
                motivo = "Sem solicitação no prontuário"
            elif cat == "justificativa":
                motivo = ""  # foi faturado por justificativa (se anexado)
            else:
                motivo = g.get("motivo") or x.get("erro") or "revisão humana"
            ex.itens.append(ExecucaoItem(
                gto=str(x.get("gto")), paciente=x.get("paciente"),
                categoria=cat, faturado=faturado, motivo=motivo,
                solicitacao=x.get("solicitacao"),
                exames_gto=", ".join(x.get("gto_exames") or []),
                exames_lidos=", ".join(g.get("exames_lidos") or []),
                n_arquivos=len(x.get("laudo_imgs") or []) + (1 if x.get("solicitacao") else 0),
            ))
        s.add(ex)
        s.commit()
        return ex.id


def listar_execucoes(limit: int = 100) -> list:
    """Histórico de execuções (consolidado) — mais recentes primeiro."""
    with SessionLocal() as s:
        rows = s.query(Execucao).order_by(Execucao.criado_em.desc()).limit(limit).all()
        return [{
            "id": e.id, "dia": e.dia, "criado_em": e.criado_em, "dry_run": e.dry_run,
            "tempo_total": e.tempo_total, "tempo_descoberta": e.tempo_descoberta,
            "tempo_download": e.tempo_download, "pendentes": e.pendentes,
            "faturadas": e.faturadas, "nao_faturadas": e.nao_faturadas,
        } for e in rows]


def get_execucao(eid: int) -> dict | None:
    """Detalhe de uma execução + itens (faturadas e não faturadas)."""
    with SessionLocal() as s:
        e = s.get(Execucao, eid)
        if not e:
            return None
        itens = [{
            "gto": it.gto, "paciente": it.paciente, "categoria": it.categoria,
            "faturado": it.faturado, "motivo": it.motivo, "solicitacao": it.solicitacao,
            "exames_gto": it.exames_gto, "exames_lidos": it.exames_lidos,
            "n_arquivos": it.n_arquivos,
        } for it in e.itens]
        return {
            "id": e.id, "dia": e.dia, "criado_em": e.criado_em, "dry_run": e.dry_run,
            "tempo_total": e.tempo_total, "tempo_descoberta": e.tempo_descoberta,
            "tempo_download": e.tempo_download, "pendentes": e.pendentes,
            "faturadas": e.faturadas, "nao_faturadas": e.nao_faturadas,
            "m_download": e.m_download, "k_leitura": e.k_leitura,
            "faturadas_itens": [i for i in itens if i["faturado"]],
            "nao_faturadas_itens": [i for i in itens if not i["faturado"]],
        }


def init_db():
    Base.metadata.create_all(engine)
    _ensure_columns()


def _ensure_columns():
    """Migração leve: adiciona colunas novas em tabelas que já existem
    (create_all não altera tabela existente). Best-effort, ignora erros."""
    from sqlalchemy import text
    alters = [
        "ALTER TABLE runs ADD COLUMN IF NOT EXISTS log TEXT",
        "ALTER TABLE runs ADD COLUMN IF NOT EXISTS plano VARCHAR(40) DEFAULT 'odontoprev'",
        "ALTER TABLE runs ADD COLUMN IF NOT EXISTS erro_msg TEXT",
        "ALTER TABLE glosa_eventos ADD COLUMN IF NOT EXISTS demo_glosado VARCHAR(20)",
        "ALTER TABLE glosa_eventos ADD COLUMN IF NOT EXISTS demo_pago BOOLEAN DEFAULT FALSE",
        "ALTER TABLE anexacao_gtos ADD COLUMN IF NOT EXISTS liberacao VARCHAR(10)",
    ]
    for a in alters:
        try:
            with engine.begin() as c:
                c.execute(text(a))
        except Exception:
            pass


def criar_run(dia: str, dry_run: bool, plano: str = "odontoprev") -> int:
    """Cria a linha da execução (status=running) e retorna o id."""
    with SessionLocal() as s:
        r = Run(dia=dia, dry_run=dry_run, status="running", plano=plano)
        s.add(r)
        s.commit()
        return r.id


def finalizar_run_ok(run_id: int, relatorio: dict, log_texto: str = None) -> None:
    """Grava resumo + itens de uma execução concluída."""
    resumo = relatorio.get("resumo", {}) or {}
    itens = relatorio.get("itens", []) or []
    with SessionLocal() as s:
        r = s.get(Run, run_id)
        if not r:
            return
        r.status = "done"
        r.finished_at = _now()
        if log_texto is not None:
            r.log = log_texto[-20000:]
        r.dry_run = bool(relatorio.get("dry_run", r.dry_run))
        for k in ("alvos", "enviados", "prontos", "erros", "sem_match",
                  "sem_laudo", "sem_imagens", "revisao_humana", "solic_anexada"):
            setattr(r, k, int(resumo.get(k, 0) or 0))
        for it in itens:
            up = it.get("upload") or {}
            s.add(RunItem(
                run_id=run_id,
                gto=str(it.get("gto", "")),
                paciente=it.get("nome_gto") or it.get("nome") or "",
                status=it.get("status", ""),
                justificativa=it.get("justificativa", ""),
                enviados=len(up.get("enviados", []) or []),
                ja_anexados=len(up.get("ja_anexados", []) or []),
                solicitacao=(it.get("solicitacao") or "")[:200],
                revisao_humana=it.get("revisao_humana", "") or "",
                detalhe=it.get("detalhe", "") or "",
            ))
        s.commit()


def finalizar_run_erro(run_id: int, msg: str, log_texto: str = None) -> None:
    with SessionLocal() as s:
        r = s.get(Run, run_id)
        if not r:
            return
        r.status = "error"
        r.finished_at = _now()
        r.erro_msg = (msg or "")[:2000]
        if log_texto is not None:
            r.log = log_texto[-20000:]
        s.commit()


def limpar_runs_travadas(horas: float = None) -> int:
    """Marca como 'error' execuções presas em 'running'. Sem `horas`: TODAS (uso no
    startup — o processo que as iniciou já morreu, são zumbis). Com `horas`: só as
    que estão em running há mais que isso (pega travamentos sem reinício)."""
    from datetime import timedelta
    with SessionLocal() as s:
        q = s.query(Run).filter(Run.status == "running")
        if horas is not None:
            q = q.filter(Run.started_at < _now() - timedelta(hours=horas))
        rs = q.all()
        for r in rs:
            r.status = "error"
            r.finished_at = _now()
            r.erro_msg = ((r.erro_msg or "") +
                          "\n[limpeza automática] execução interrompida (não finalizou).").strip()
        s.commit()
        return len(rs)


def runs_recentes(limite: int = 15) -> list:
    """Últimas execuções de QUALQUER status (done/error/running) — p/ diagnóstico."""
    with SessionLocal() as s:
        rs = (s.query(Run).order_by(Run.started_at.desc()).limit(limite).all())
        out = []
        for r in rs:
            d = _run_to_dict(r)
            d["erro_msg"] = r.erro_msg
            out.append(d)
        return out


def run_log(run_id: int) -> dict:
    """Log completo + erro de uma execução específica."""
    with SessionLocal() as s:
        r = s.get(Run, run_id)
        if not r:
            return {}
        return {"id": r.id, "dia": r.dia, "status": r.status,
                "erro_msg": r.erro_msg, "log": r.log}


# ── Consultas para o dashboard ────────────────────────────────────────────────

def _run_to_dict(r: Run) -> dict:
    return {
        "id": r.id, "plano": r.plano, "dia": r.dia, "dry_run": r.dry_run, "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "alvos": r.alvos, "enviados": r.enviados, "prontos": r.prontos,
        "erros": r.erros, "sem_match": r.sem_match, "sem_laudo": r.sem_laudo,
        "sem_imagens": r.sem_imagens, "revisao_humana": r.revisao_humana,
        "solic_anexada": r.solic_anexada, "erro_msg": r.erro_msg,
    }


def ultimas_runs(limite: int = 10) -> list:
    with SessionLocal() as s:
        rs = (s.query(Run).filter(Run.status == "done")
              .order_by(Run.finished_at.desc()).limit(limite).all())
        return [_run_to_dict(r) for r in rs]


def status_por_plano() -> dict:
    """Para cada plano (slug), a última execução concluída — p/ a lista de planos.
    Retorna {slug: run_dict_resumido}."""
    with SessionLocal() as s:
        rs = (s.query(Run).filter(Run.status == "done")
              .order_by(Run.finished_at.desc()).limit(200).all())
        out = {}
        for r in rs:
            if r.plano not in out:
                out[r.plano] = _run_to_dict(r)
        return out


def run_mais_recente(dia: str = None, plano: str = None):
    """Última execução concluída (de um dia/plano específico, se informado)."""
    with SessionLocal() as s:
        q = s.query(Run).filter(Run.status == "done")
        if dia:
            q = q.filter(Run.dia == dia)
        if plano:
            q = q.filter(Run.plano == plano)
        r = q.order_by(Run.finished_at.desc()).first()
        if not r:
            return None
        d = _run_to_dict(r)
        d["itens"] = [{
            "gto": it.gto, "paciente": it.paciente, "status": it.status,
            "justificativa": it.justificativa, "enviados": it.enviados,
            "ja_anexados": it.ja_anexados, "solicitacao": it.solicitacao,
            "revisao_humana": it.revisao_humana, "detalhe": it.detalhe,
        } for it in r.itens]
        return d


def run_detalhe(run_id: int):
    """Uma execução específica (por id) + todos os itens — para o relatório."""
    with SessionLocal() as s:
        r = s.get(Run, run_id)
        if not r:
            return None
        d = _run_to_dict(r)
        d["dia"] = r.dia
        d["plano"] = r.plano
        d["log"] = r.log
        d["itens"] = [{
            "gto": it.gto, "paciente": it.paciente, "status": it.status,
            "justificativa": it.justificativa, "enviados": it.enviados,
            "ja_anexados": it.ja_anexados, "solicitacao": it.solicitacao,
            "revisao_humana": it.revisao_humana, "detalhe": it.detalhe,
        } for it in r.itens]
        return d


def fila_revisao(limite: int = 30) -> list:
    """Itens em revisão humana das execuções mais recentes (não-dry-run)."""
    with SessionLocal() as s:
        ultima = (s.query(Run).filter(Run.status == "done", Run.dry_run == False)  # noqa: E712
                  .order_by(Run.finished_at.desc()).first())
        if not ultima:
            return []
        its = [it for it in ultima.itens
               if (it.revisao_humana or "").strip() or it.status in ("SEM_MATCH", "AMBIGUO")]
        out = []
        for it in its[:limite]:
            out.append({
                "gto": it.gto, "paciente": it.paciente, "status": it.status,
                "motivo": it.revisao_humana or (
                    "Sem correspondência no PRORADIS" if it.status == "SEM_MATCH"
                    else "Nome ambíguo" if it.status == "AMBIGUO" else it.detalhe),
                "dia": ultima.dia,
            })
        return out


def serie_semana() -> list:
    """Total de 'enviados' por dia processado, nas últimas execuções (até 7 dias)."""
    with SessionLocal() as s:
        # agrupa pela coluna 'dia' (string DD/MM/AAAA), pegando a melhor run de cada dia
        rs = (s.query(Run).filter(Run.status == "done", Run.dry_run == False)  # noqa: E712
              .order_by(Run.finished_at.desc()).limit(60).all())
        por_dia = {}
        for r in rs:
            if r.dia not in por_dia:  # primeira (mais recente) por dia
                por_dia[r.dia] = r.enviados
        # ordena por data real
        def _key(d):
            try:
                dd, mm, yy = d.split("/")
                return (int(yy), int(mm), int(dd))
            except Exception:
                return (0, 0, 0)
        dias = sorted(por_dia.keys(), key=_key)[-7:]
        return [{"dia": d, "enviados": por_dia[d]} for d in dias]


def totais_gerais() -> dict:
    with SessionLocal() as s:
        tot_env = s.query(func.coalesce(func.sum(Run.enviados), 0)).filter(
            Run.status == "done", Run.dry_run == False).scalar()  # noqa: E712
        n_runs = s.query(func.count(Run.id)).filter(Run.status == "done").scalar()
        return {"total_enviados": int(tot_env or 0), "total_execucoes": int(n_runs or 0)}


# ── Agregações por PERÍODO e por PLANO (gráfico empilhado + detalhe) ───────────

def _dia_to_date(dia: str):
    """'DD/MM/AAAA' -> date (ou None)."""
    try:
        return datetime.strptime((dia or "").strip(), "%d/%m/%Y").date()
    except Exception:
        return None


def _bucket(status: str) -> str:
    """Mapeia o status de uma GTO num balde mutuamente exclusivo (p/ empilhar)."""
    st = (status or "").upper()
    if st in ("ENVIADO", "JA_ANEXADO"):
        return "anexadas"
    if st == "PRONTO":
        return "simulacao"
    if st in ("SEM_LAUDO", "SEM_IMAGENS"):
        return "sem_laudo"
    if st in ("SEM_MATCH", "AMBIGUO", "ERRO", "ERRO_UPLOAD"):
        return "erros"
    return "outros"


def _melhores_runs_periodo(s, de_iso: str, ate_iso: str, plano: str = None):
    """Melhor run por (plano, dia) com 'dia' dentro de [de, ate]. Prefere
    execução real (não-dry-run) e, em empate, a mais recente."""
    try:
        de = datetime.fromisoformat(de_iso).date()
        ate = datetime.fromisoformat(ate_iso).date()
    except Exception:
        return []
    q = s.query(Run).filter(Run.status == "done")
    if plano:
        q = q.filter(Run.plano == plano)
    runs = q.order_by(Run.finished_at.desc()).all()
    best = {}
    for r in runs:
        d = _dia_to_date(r.dia)
        if not d or not (de <= d <= ate):
            continue
        key = (r.plano, r.dia)
        cur = best.get(key)
        if cur is None:
            best[key] = r          # já vem do mais recente p/ mais antigo
        elif cur.dry_run and not r.dry_run:
            best[key] = r          # troca simulação por execução real
    return list(best.values())


def gtos_por_plano_periodo(de_iso: str, ate_iso: str) -> dict:
    """Para cada plano, contagem empilhada por desfecho no período.
    Retorna {slug: {anexadas, sem_laudo, erros, simulacao, revisao, total, dias}}."""
    with SessionLocal() as s:
        out = {}
        for r in _melhores_runs_periodo(s, de_iso, ate_iso):
            a = out.setdefault(r.plano, {"anexadas": 0, "sem_laudo": 0, "erros": 0,
                                         "simulacao": 0, "revisao": 0, "total": 0,
                                         "dias": 0})
            a["dias"] += 1
            for it in r.itens:
                a["total"] += 1
                a[_bucket(it.status)] = a.get(_bucket(it.status), 0) + 1
                if (it.revisao_humana or "").strip():
                    a["revisao"] += 1
        return out


def itens_plano_periodo(plano: str, de_iso: str, ate_iso: str) -> dict:
    """Itens (GTOs) de um plano no período, agrupados por dia — p/ a tela de detalhe."""
    with SessionLocal() as s:
        runs = _melhores_runs_periodo(s, de_iso, ate_iso, plano=plano)
        runs.sort(key=lambda r: (_dia_to_date(r.dia) or datetime.min.date()), reverse=True)
        dias = []
        tot = {"anexadas": 0, "sem_laudo": 0, "erros": 0, "simulacao": 0,
               "revisao": 0, "total": 0}
        for r in runs:
            its = []
            for it in r.itens:
                its.append({
                    "gto": it.gto, "paciente": it.paciente, "status": it.status,
                    "bucket": _bucket(it.status), "enviados": it.enviados,
                    "ja_anexados": it.ja_anexados, "detalhe": it.detalhe,
                    "revisao_humana": it.revisao_humana, "solicitacao": it.solicitacao,
                })
                tot["total"] += 1
                tot[_bucket(it.status)] = tot.get(_bucket(it.status), 0) + 1
                if (it.revisao_humana or "").strip():
                    tot["revisao"] += 1
            dias.append({"dia": r.dia, "run_id": r.id, "dry_run": r.dry_run,
                         "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                         "itens": its})
        return {"plano": plano, "de": de_iso, "ate": ate_iso, "totais": tot, "dias": dias}


# ── Glosas / Recurso ──────────────────────────────────────────────────────────

# Rótulos amigáveis das situações (ordem = prioridade de exibição).
GLOSA_SITUACOES = [
    ("A_RECORRER", "A recorrer"),
    ("RECURSO_OU_RESOLVIDA", "Recurso enviado / em análise"),
    ("RECURSO_REJEITADO", "Recurso recusado (refazer)"),
    ("RESOLVIDA", "Resolvida (paga)"),
    ("GLOSA_CONFIRMADA", "Glosa confirmada"),
    ("NAO_RECURSAVEL", "Não recursável"),
    ("GLOSADA", "Glosada"),
]


def salvar_glosas(lote: str, dia: str, eventos: list) -> int:
    """Grava os eventos de glosa de uma extração (um 'lote'). Substitui os
    eventos das MESMAS unidades naquele lote (idempotente por re-run do lote)."""
    with SessionLocal() as s:
        contas = {e.get("conta") for e in eventos}
        for conta in contas:
            s.query(GlosaEvento).filter(
                GlosaEvento.lote == lote, GlosaEvento.conta == conta).delete()
        for e in eventos:
            s.add(GlosaEvento(
                lote=lote, dia=dia, conta=e.get("conta", ""), unidade=e.get("unidade", ""),
                ficha=str(e.get("ficha", "")), paciente=(e.get("paciente") or "")[:200],
                evento_cod=e.get("evento_cod", ""), evento=(e.get("evento") or "")[:200],
                glosa_cod=e.get("glosa_cod", ""), glosa_motivo=(e.get("glosa_motivo") or "")[:200],
                recurso_estado=e.get("recurso_estado", ""), situacao=e.get("situacao", ""),
                demo_glosado=("" if e.get("demo_glosado") is None
                              else f"{e.get('demo_glosado'):.2f}"),
                demo_pago=bool(e.get("demo_pago")),
            ))
        s.commit()
        return len(eventos)


def prune_glosa(keep_lote: str, dia: str) -> int:
    """Remove lotes de glosa do MESMO mês de `dia` (sweep cumulativo 1º→data),
    exceto keep_lote. Mantém lotes de outros meses."""
    if not dia or dia.count("/") != 2:
        return 0
    mes = "/".join(dia.split("/")[1:])  # MM/YYYY
    with SessionLocal() as s:
        lotes = [r[0] for r in s.query(GlosaEvento.lote).filter(
            GlosaEvento.dia.like("%/" + mes), GlosaEvento.lote != keep_lote).distinct().all()]
        n = 0
        for l in lotes:
            n += s.query(GlosaEvento).filter(
                GlosaEvento.lote == l).delete(synchronize_session=False)
        s.commit()
        return n


def glosa_lotes(limite: int = 30) -> list:
    """Lotes de extração (mais recente primeiro) com data e total de eventos."""
    with SessionLocal() as s:
        rows = (s.query(GlosaEvento.lote, GlosaEvento.dia,
                        func.count(GlosaEvento.id), func.max(GlosaEvento.captured_at))
                .group_by(GlosaEvento.lote, GlosaEvento.dia)
                .order_by(func.max(GlosaEvento.captured_at).desc()).limit(limite).all())
        return [{"lote": r[0], "dia": r[1], "total": int(r[2]),
                 "captured_at": r[3].isoformat() if r[3] else None} for r in rows]


def _lote_atual(s, lote: str = None) -> str:
    if lote:
        return lote
    r = (s.query(GlosaEvento.lote).order_by(GlosaEvento.captured_at.desc()).first())
    return r[0] if r else None


def glosa_panorama(lote: str = None) -> dict:
    """Resumo do panorama de glosas de um lote (default = mais recente):
    totais por situação, por unidade e por motivo."""
    with SessionLocal() as s:
        lote = _lote_atual(s, lote)
        if not lote:
            return {"lote": None, "dia": None, "total": 0, "por_situacao": {},
                    "por_unidade": [], "por_motivo": [], "situacoes": GLOSA_SITUACOES,
                    "total_glosado": 0.0}
        evs = s.query(GlosaEvento).filter(GlosaEvento.lote == lote).all()
        dia = evs[0].dia if evs else None
        por_situacao = {k: 0 for k, _ in GLOSA_SITUACOES}
        por_unidade, por_motivo = {}, {}
        ordem = {k: i for i, (k, _) in enumerate(GLOSA_SITUACOES)}
        glosado_ficha = {}   # (unidade, ficha) -> R$ glosado (valor é por GUIA: 1x)
        sit_ficha = {}       # (unidade, ficha) -> situação representativa (maior prioridade)
        for e in evs:
            por_situacao[e.situacao] = por_situacao.get(e.situacao, 0) + 1
            u = por_unidade.setdefault(e.unidade, {"unidade": e.unidade, "total": 0,
                                                   "glosado": 0.0,
                                                   **{k: 0 for k, _ in GLOSA_SITUACOES}})
            u["total"] += 1
            u[e.situacao] = u.get(e.situacao, 0) + 1
            m = por_motivo.setdefault(e.glosa_cod, {"glosa_cod": e.glosa_cod,
                                                    "glosa_motivo": e.glosa_motivo, "total": 0})
            m["total"] += 1
            key = (e.unidade, e.ficha)
            cur = sit_ficha.get(key)
            if cur is None or ordem.get(e.situacao, 99) < ordem.get(cur, 99):
                sit_ficha[key] = e.situacao
            try:
                v = float(e.demo_glosado) if e.demo_glosado else 0.0
            except (TypeError, ValueError):
                v = 0.0
            if v > 0:
                glosado_ficha.setdefault(key, v)
        glosado_situacao = {k: 0.0 for k, _ in GLOSA_SITUACOES}
        for key, v in glosado_ficha.items():
            if key[0] in por_unidade:
                por_unidade[key[0]]["glosado"] += v
            s = sit_ficha.get(key)
            if s in glosado_situacao:
                glosado_situacao[s] += v
        return {
            "lote": lote, "dia": dia, "total": len(evs),
            "por_situacao": por_situacao,
            "glosado_situacao": {k: round(v, 2) for k, v in glosado_situacao.items()},
            "por_unidade": sorted(por_unidade.values(), key=lambda x: -x["total"]),
            "por_motivo": sorted(por_motivo.values(), key=lambda x: -x["total"]),
            "situacoes": GLOSA_SITUACOES,
            "total_glosado": round(sum(glosado_ficha.values()), 2),
            "guias_glosado": len(glosado_ficha),
        }


# ── Anexação / Faturamento (varredura só-leitura das GTOs) ────────────────────

ANEXACAO_CATEGORIAS = [
    ("FATURADA", "Faturada (2+ anexos)"),
    ("A_FATURAR", "A faturar (só 1 anexo)"),
    ("SEM_ANEXO", "Sem anexo"),
    ("LIBERADA", "Liberada p/ assinatura"),
    ("CANCELADA", "Cancelada"),
    ("ERRO", "Erro de leitura"),
]


def salvar_anexacao(lote: str, de: str, ate: str, gtos: list) -> int:
    with SessionLocal() as s:
        for conta in {g.get("conta") for g in gtos}:
            s.query(AnexacaoGto).filter(
                AnexacaoGto.lote == lote, AnexacaoGto.conta == conta).delete()
        for g in gtos:
            s.add(AnexacaoGto(
                lote=lote, de=de, ate=ate, conta=g.get("conta", ""),
                unidade=g.get("unidade", ""), gto=str(g.get("gto", "")),
                paciente=(g.get("paciente") or "")[:200], liberacao=(g.get("liberacao") or "")[:10],
                status=(g.get("status") or "")[:80],
                qtd_anexos=int(g.get("qtd_anexos", 0) or 0), categoria=g.get("categoria", ""),
            ))
        s.commit()
        return len(gtos)


def prune_anexacao(keep_lote: str, de: str) -> int:
    """Remove varreduras antigas do MESMO início de período (de) — a varredura é
    cumulativa (1º do mês até hoje), então a nova substitui as anteriores do mês.
    Mantém varreduras de outros períodos (queries intencionais)."""
    with SessionLocal() as s:
        n = s.query(AnexacaoGto).filter(
            AnexacaoGto.de == de, AnexacaoGto.lote != keep_lote).delete(
            synchronize_session=False)
        s.commit()
        return n


def anexacao_lotes(limite: int = 30) -> list:
    with SessionLocal() as s:
        rows = (s.query(AnexacaoGto.lote, AnexacaoGto.de, AnexacaoGto.ate,
                        func.count(AnexacaoGto.id), func.max(AnexacaoGto.captured_at))
                .group_by(AnexacaoGto.lote, AnexacaoGto.de, AnexacaoGto.ate)
                .order_by(func.max(AnexacaoGto.captured_at).desc()).limit(limite).all())
        return [{"lote": r[0], "de": r[1], "ate": r[2], "total": int(r[3]),
                 "captured_at": r[4].isoformat() if r[4] else None} for r in rows]


def _lote_anexacao(s, lote: str = None) -> str:
    if lote:
        return lote
    r = s.query(AnexacaoGto.lote).order_by(AnexacaoGto.captured_at.desc()).first()
    return r[0] if r else None


def anexacao_panorama(lote: str = None) -> dict:
    with SessionLocal() as s:
        lote = _lote_anexacao(s, lote)
        if not lote:
            return {"lote": None, "de": None, "ate": None, "total": 0,
                    "por_categoria": {}, "por_unidade": [], "categorias": ANEXACAO_CATEGORIAS}
        gs = s.query(AnexacaoGto).filter(AnexacaoGto.lote == lote).all()
        por_cat = {k: 0 for k, _ in ANEXACAO_CATEGORIAS}
        por_uni = {}
        for g in gs:
            por_cat[g.categoria] = por_cat.get(g.categoria, 0) + 1
            u = por_uni.setdefault(g.unidade, {"unidade": g.unidade, "total": 0,
                                               **{k: 0 for k, _ in ANEXACAO_CATEGORIAS}})
            u["total"] += 1
            u[g.categoria] = u.get(g.categoria, 0) + 1
        return {
            "lote": lote, "de": gs[0].de if gs else None, "ate": gs[0].ate if gs else None,
            "total": len(gs), "por_categoria": por_cat,
            "por_unidade": sorted(por_uni.values(), key=lambda x: -x["total"]),
            "categorias": ANEXACAO_CATEGORIAS,
        }


def anexacao_gtos(lote: str = None, unidade: str = None, categoria: str = None) -> list:
    with SessionLocal() as s:
        lote = _lote_anexacao(s, lote)
        if not lote:
            return []
        q = s.query(AnexacaoGto).filter(AnexacaoGto.lote == lote)
        if unidade:
            q = q.filter(AnexacaoGto.unidade == unidade)
        if categoria:
            q = q.filter(AnexacaoGto.categoria == categoria)
        q = q.order_by(AnexacaoGto.unidade, AnexacaoGto.categoria, AnexacaoGto.gto)
        return [{"unidade": g.unidade, "conta": g.conta, "gto": g.gto,
                 "paciente": g.paciente, "liberacao": g.liberacao or "", "status": g.status,
                 "qtd_anexos": g.qtd_anexos, "categoria": g.categoria} for g in q.all()]


def glosa_eventos(lote: str = None, unidade: str = None, situacao: str = None) -> list:
    """Lista de eventos de glosa de um lote, com filtros opcionais — p/ tabela/export."""
    with SessionLocal() as s:
        lote = _lote_atual(s, lote)
        if not lote:
            return []
        q = s.query(GlosaEvento).filter(GlosaEvento.lote == lote)
        if unidade:
            q = q.filter(GlosaEvento.unidade == unidade)
        if situacao:
            q = q.filter(GlosaEvento.situacao == situacao)
        q = q.order_by(GlosaEvento.unidade, GlosaEvento.situacao, GlosaEvento.ficha)
        return [{
            "unidade": e.unidade, "conta": e.conta, "ficha": e.ficha,
            "paciente": e.paciente, "evento": e.evento, "glosa_cod": e.glosa_cod,
            "glosa_motivo": e.glosa_motivo, "recurso_estado": e.recurso_estado,
            "situacao": e.situacao, "demo_glosado": e.demo_glosado, "demo_pago": e.demo_pago,
        } for e in q.all()]
