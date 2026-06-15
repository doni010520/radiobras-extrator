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
