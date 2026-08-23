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
from datetime import datetime, timezone, date, timedelta

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
    paciente = Column(Text)
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


class GuiaDesfecho(Base):
    """DESFECHO na RedeUna de uma guia que NÓS faturamos: pago/glosado/cancelado +
    detalhe da glosa (motivo, como recursar) e prazo de recurso. Um 'lote' por
    atualização; o painel usa o lote mais recente. Âncora = ExecucaoItem faturado."""
    __tablename__ = "guia_desfechos"
    id = Column(Integer, primary_key=True)
    lote = Column(String(20), index=True)           # YYYYMMDDHHMMSS da atualização
    captured_at = Column(DateTime(timezone=True), default=_now)
    conta = Column(String(20), index=True)
    unidade = Column(String(60), index=True)
    gto = Column(String(30), index=True)            # nº da guia
    paciente = Column(String(200))
    dia_faturado = Column(String(10))               # quando NÓS faturamos (DD/MM/AAAA)
    status = Column(String(20), index=True)         # PAGA | GLOSADA | CANCELADA | AGUARDANDO
    valor_bruto = Column(String(20))
    valor_glosado = Column(String(20))
    valor_pago = Column(String(20))
    data_repasse = Column(String(10))               # DD/MM/AAAA (do Demonstrativo)
    # detalhe da glosa (só quando GLOSADA)
    glosa_cod = Column(String(10))
    glosa_motivo = Column(String(200))
    como_recursar = Column(Text)                    # orientação "Como Recursar?" do relatório
    recurso_estado = Column(String(20))             # RECURSAVEL | SEM_GLOSADO | PRESCRITO | ...
    ortodontia = Column(Boolean, default=False)     # janela 120d (orto) x 90d (demais)
    prazo_limite = Column(String(10))               # DD/MM/AAAA (data-limite do recurso)
    prazo_dias = Column(Integer)                    # dias restantes (negativo = prescrito)
    prescrito = Column(Boolean, default=False)


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
    conta = Column(String(20), index=True)           # código da conta/plano (388336, 397950, 410923)
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
    # LOG TECNICO COMPLETO da execucao. Antes vivia SO em memoria (dict _esteira_jobs)
    # e sumia no restart do container: quando a operadora relatava um problema, nao
    # havia mais como saber o que tinha acontecido. Caso LOARA (29/07).
    log = Column(Text)
    # Execucao que FALHOU tambem fica registrada. Antes, erro em rodar_esteira
    # (login bloqueado, PRORADIS sem laudo do dia, token nao capturado) fazia a
    # execucao desaparecer: nao virava faturamento, nao virava pendencia, nao
    # virava nada. A operadora dizia "eu rodei" e nao havia como confirmar.
    erro = Column(Text)
    gemini_chamadas = Column(Integer, default=0)
    gemini_tokens_in = Column(Integer, default=0)
    gemini_tokens_out = Column(Integer, default=0)
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
    solicitacao = Column(Text)       # arquivo anexado (quando AUTO)
    exames_gto = Column(Text)
    exames_lidos = Column(Text)
    n_arquivos = Column(Integer, default=0)
    # EVIDENCIA por guia — o que o sistema realmente viu e fez. Sem isso, uma
    # pendencia so podia ser investigada com o log em memoria, que nao sobrevive.
    arquivos_plano = Column(Text)      # o que iria/foi anexado
    excluidos = Column(Text)           # o que foi retirado do plano, e implicitamente por que
    funil = Column(Text)        # anexos do prontuario -> candidatos -> descartados
    # TEXT, nao String(160). Quando a guia trava no nome, aqui vai o que a IA leu em
    # CADA anexo — texto que passa facil de 160 caracteres. O Postgres recusava a
    # linha e, com ela, a EXECUCAO INTEIRA deixava de ser gravada: a operadora via
    # o resultado na tela, sem botao de relatorio e sem motivos, e nada ficava no
    # historico. Aconteceu duas vezes com 24/07 Centro em 30/07.
    paciente_lido = Column(Text)         # o nome que a IA leu no documento
    data_exame_real = Column(String(10))  # se o exame veio de outro dia
    execucao = relationship("Execucao", back_populates="itens")


class Usuario(Base):
    """Usuário do sistema (login por usuário+senha, sem e-mail)."""
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    username = Column(String(60), unique=True, index=True, nullable=False)
    senha_hash = Column(String(255), nullable=False)
    nome = Column(String(120))
    role = Column(String(20), default="user")     # admin | user
    ativo = Column(Boolean, default=True)
    criado_em = Column(DateTime(timezone=True), default=_now)
    ultimo_login = Column(DateTime(timezone=True))


def criar_usuario(username: str, senha: str, nome: str = "", role: str = "user") -> dict:
    """Cria um usuário. Levanta ValueError se username já existe ou inválido."""
    from werkzeug.security import generate_password_hash
    username = (username or "").strip().lower()
    if not username or not senha:
        raise ValueError("Usuário e senha são obrigatórios.")
    if len(senha) < 4:
        raise ValueError("A senha precisa ter ao menos 4 caracteres.")
    with SessionLocal() as s:
        if s.query(Usuario).filter(Usuario.username == username).first():
            raise ValueError("Esse usuário já existe.")
        u = Usuario(username=username, senha_hash=generate_password_hash(senha),
                    nome=(nome or "").strip() or username, role=role)
        s.add(u); s.commit()
        return {"id": u.id, "username": u.username, "nome": u.nome, "role": u.role}


def autenticar(username: str, senha: str) -> dict | None:
    """Valida login. Retorna o usuário (dict) ou None."""
    from werkzeug.security import check_password_hash
    username = (username or "").strip().lower()
    with SessionLocal() as s:
        u = s.query(Usuario).filter(Usuario.username == username,
                                    Usuario.ativo == True).first()  # noqa: E712
        if not u or not check_password_hash(u.senha_hash, senha or ""):
            return None
        u.ultimo_login = _now(); s.commit()
        return {"id": u.id, "username": u.username, "nome": u.nome, "role": u.role}


def get_usuario(uid: int) -> dict | None:
    with SessionLocal() as s:
        u = s.get(Usuario, uid)
        if not u or not u.ativo:
            return None
        return {"id": u.id, "username": u.username, "nome": u.nome, "role": u.role}


def listar_usuarios() -> list:
    with SessionLocal() as s:
        return [{"id": u.id, "username": u.username, "nome": u.nome, "role": u.role,
                 "ativo": u.ativo, "ultimo_login": u.ultimo_login}
                for u in s.query(Usuario).order_by(Usuario.username).all()]


def set_usuario_ativo(uid: int, ativo: bool):
    with SessionLocal() as s:
        u = s.get(Usuario, uid)
        if u:
            u.ativo = ativo; s.commit()


def resetar_senha(uid: int, nova_senha: str):
    from werkzeug.security import generate_password_hash
    if len(nova_senha or "") < 4:
        raise ValueError("A senha precisa ter ao menos 4 caracteres.")
    with SessionLocal() as s:
        u = s.get(Usuario, uid)
        if u:
            u.senha_hash = generate_password_hash(nova_senha); s.commit()


def _seed_admin():
    """Cria o admin inicial se não houver nenhum usuário (credenciais via ENV
    ADMIN_USER/ADMIN_PASSWORD, com fallback)."""
    with SessionLocal() as s:
        if s.query(Usuario).count() > 0:
            return
    import os
    user = (os.environ.get("ADMIN_USER") or "admin").strip().lower()
    senha = os.environ.get("ADMIN_PASSWORD")
    if not senha:
        # O fallback fixo ("radiobras2026") estava num repositório PÚBLICO: qualquer
        # banco novo/limpo nascia com admin de senha conhecida. Em produção, sem
        # ADMIN_PASSWORD, não cria — melhor não ter admin do que ter um previsível.
        if os.environ.get("RB_PRODUCAO") == "1" or os.environ.get("FLASK_ENV") == "production":
            print("[db] ADMIN_PASSWORD não definida — admin inicial NÃO criado. "
                  "Defina a variável e reinicie.", flush=True)
            return
        import secrets as _secrets
        senha = _secrets.token_urlsafe(12)
        print(f"[db] ADMIN_PASSWORD ausente (dev): senha gerada -> {senha}", flush=True)
    try:
        criar_usuario(user, senha, nome="Administrador", role="admin")
        print(f"[db] usuário admin inicial criado: {user}", flush=True)
    except Exception as e:
        print(f"[db] seed admin falhou: {e}", flush=True)


class Pendencia(Base):
    """Item que precisa de revisão humana (não faturado numa execução).
    Backlog: dedupe por (conta, dia, gto); fecha sozinho se depois for faturado."""
    __tablename__ = "pendencias"
    id = Column(Integer, primary_key=True)
    execucao_id = Column(Integer, ForeignKey("execucoes.id", ondelete="SET NULL"))
    conta = Column(String(20), index=True)
    dia = Column(String(10), index=True)
    gto = Column(String(30), index=True)
    paciente = Column(String(200))
    categoria = Column(String(20))          # sem_solicitacao | revisao | ...
    motivo = Column(Text)
    criado_em = Column(DateTime(timezone=True), default=_now, index=True)
    resolvido = Column(Boolean, default=False, index=True)
    resolvido_em = Column(DateTime(timezone=True))
    resolvido_por = Column(String(60))      # username ou 'sistema'
    obs = Column(Text)


class AvisoSemGuia(Base):
    """Exame com laudo PRONTO no PRORADIS que NÃO corresponde a nenhuma guia do
    convênio (particular, ou guia esquecida). NÃO é pendência (não é 'corrigir pra
    faturar'): é AVISO ao dono, que confirma com 'Ciente'. Dedupe por
    (conta, dia, accession) — re-run não duplica; um Ciente não reabre."""
    __tablename__ = "avisos_sem_guia"
    id = Column(Integer, primary_key=True)
    execucao_id = Column(Integer, ForeignKey("execucoes.id", ondelete="SET NULL"))
    conta = Column(String(20), index=True)
    dia = Column(String(10), index=True)
    gto = Column(String(30))                 # a guia sob a qual apareceu (contexto)
    paciente = Column(String(200))
    exame = Column(String(200))
    accession = Column(String(30), index=True)
    criado_em = Column(DateTime(timezone=True), default=_now, index=True)
    visto = Column(Boolean, default=False, index=True)
    visto_em = Column(DateTime(timezone=True))
    visto_por = Column(String(60))           # username


def _sync_avisos(s, conta, dia, exec_id, avisos_info):
    """Cria avisos 'exame sem guia' a partir dos itens de uma execução real.
    avisos_info: lista de dict {accession, exame, gto, paciente}. Dedupe por
    (conta, dia, accession); NÃO reabre um aviso já criado (visto ou não)."""
    for a in avisos_info:
        acc = str(a.get("accession") or "")
        if not acc:
            continue
        try:
            with s.begin_nested():
                ja = (s.query(AvisoSemGuia)
                      .filter(AvisoSemGuia.conta == conta, AvisoSemGuia.dia == dia,
                              AvisoSemGuia.accession == acc).first())
                if ja:
                    continue                 # já existe -> não duplica
                s.add(AvisoSemGuia(
                    execucao_id=exec_id, conta=conta, dia=dia,
                    gto=str(a.get("gto") or ""), paciente=a.get("paciente"),
                    exame=a.get("exame"), accession=acc))
        except Exception as e:
            print(f"[db] aviso sem guia {acc} falhou: {str(e)[:80]}", flush=True)
    s.commit()


def contar_avisos_nao_vistos():
    with SessionLocal() as s:
        return s.query(AvisoSemGuia).filter(AvisoSemGuia.visto == False).count()


def listar_avisos(status="nao_vistos", limit=3000):
    with SessionLocal() as s:
        q = s.query(AvisoSemGuia)
        if status == "nao_vistos":
            q = q.filter(AvisoSemGuia.visto == False)
        elif status == "vistos":
            q = q.filter(AvisoSemGuia.visto == True)
        rows = q.order_by(AvisoSemGuia.criado_em.desc()).limit(limit).all()
        return [{"id": r.id, "conta": r.conta, "dia": r.dia, "gto": r.gto,
                 "paciente": r.paciente, "exame": r.exame, "accession": r.accession,
                 "criado_em": r.criado_em, "visto": r.visto,
                 "visto_em": r.visto_em, "visto_por": r.visto_por} for r in rows]


def marcar_aviso_visto(aviso_id, quem):
    with SessionLocal() as s:
        r = s.get(AvisoSemGuia, aviso_id)
        if r and not r.visto:
            r.visto = True; r.visto_em = _now(); r.visto_por = (quem or "?")[:60]
            s.commit()
        return s.query(AvisoSemGuia).filter(AvisoSemGuia.visto == False).count()


def marcar_todos_avisos_vistos(quem):
    with SessionLocal() as s:
        for r in s.query(AvisoSemGuia).filter(AvisoSemGuia.visto == False).all():
            r.visto = True; r.visto_em = _now(); r.visto_por = (quem or "?")[:60]
        s.commit()
    return 0


def _sync_pendencias(s, conta, dia, exec_id, itens_info):
    """Cria/atualiza o backlog de revisão a partir dos itens de uma execução real.
    itens_info: lista de (gto, paciente, categoria, motivo, faturado)."""
    # Um SAVEPOINT por item: antes era um commit único no fim, então um erro em
    # QUALQUER item (constraint, dado fora do tamanho) descartava o backlog INTEIRO
    # daquela execução — as pendências simplesmente não eram criadas.
    falhas = 0
    for gto, paciente, cat, motivo, faturado in itens_info:
        try:
            with s.begin_nested():
                p = (s.query(Pendencia)
                     .filter(Pendencia.conta == conta, Pendencia.dia == dia,
                             Pendencia.gto == gto)
                     .first())
                if faturado:
                    if p and not p.resolvido:    # foi anexado depois -> fecha sozinho
                        p.resolvido = True
                        p.resolvido_em = _now()
                        p.resolvido_por = "sistema"
                elif p:                          # já existe pendência
                    if not p.resolvido:          # não reabre se resolvida à mão
                        p.motivo = motivo; p.categoria = cat; p.execucao_id = exec_id
                else:
                    s.add(Pendencia(execucao_id=exec_id, conta=conta, dia=dia, gto=gto,
                                    paciente=paciente, categoria=cat, motivo=motivo))
        except Exception as e:
            falhas += 1
            print(f"[db] pendencia GTO {gto} falhou: {str(e)[:100]}", flush=True)
    s.commit()
    if falhas:
        print(f"[db] {falhas} pendencia(s) falharam; as demais foram gravadas", flush=True)


def contar_pendencias_abertas() -> int:
    try:
        with SessionLocal() as s:
            return s.query(Pendencia).filter(Pendencia.resolvido == False).count()  # noqa: E712
    except Exception:
        return 0


def tentativas_por_gtos(gtos: list) -> dict:
    """{gto: nº de vezes que a esteira olhou e NÃO faturou}. Alimenta classe_efetiva
    p/ saber se um transitório já esgotou o retry (aí deixa de ser 'nosso')."""
    from sqlalchemy import func
    gs = {str(g) for g in (gtos or []) if g}
    if not gs:
        return {}
    with SessionLocal() as s:
        rows = (s.query(ExecucaoItem.gto, func.count(ExecucaoItem.id))
                .filter(ExecucaoItem.gto.in_(gs),
                        ExecucaoItem.faturado == False)  # noqa: E712
                .group_by(ExecucaoItem.gto).all())
        return {str(g): int(n) for g, n in rows}


def eh_pendencia_front(motivo: str, categoria: str = "", tentativas: int = 0) -> bool:
    """Regra do dono (13/08, ENDURECIDA em 22/08): o painel da RadioBras mostra SÓ o
    que é DELES — aguardando terceiro ou a conferir.

    Falha NOSSA nunca entra: nem em reprocessamento, nem depois de esgotar o retry.
    Antes a esgotada voltava pro front como "Investigar" e caía no colo do operador,
    que não tem o que fazer com bug nosso — pedir pedido novo à clínica seria trabalho
    jogado fora, porque o documento certo já está lá. Esgotou → o DONO é avisado no
    WhatsApp, não a operação.

    `tentativas` fica na assinatura por compatibilidade: a decisão não depende mais do
    estado do retry (quem é nosso é nosso desde a primeira falha)."""
    return not eh_nosso(motivo, categoria)


def contar_pendencias_front(so_no_prazo: bool = False, prazo: int = 7) -> int:
    """Nº de pendências que o usuário VÊ no front (sem as nossas em reprocessamento).
    `so_no_prazo=True` conta SÓ as dentro do prazo (exclui as vencidas) — é o
    'número atual' que o dono pediu (13/08): o topo não mistura o que ainda dá pra
    fazer com o que já venceu."""
    import datetime as _dt
    try:
        with SessionLocal() as s:
            rows = s.query(Pendencia).filter(Pendencia.resolvido == False).all()  # noqa: E712
        uniq = {}
        for r in rows:
            uniq[(r.conta, r.dia, r.gto)] = r
        itens = list(uniq.values())
        tent = tentativas_por_gtos([r.gto for r in itens])
        hoje = _dt.date.today()
        n = 0
        for r in itens:
            if not eh_pendencia_front(r.motivo or "", r.categoria or "", tent.get(str(r.gto), 0)):
                continue
            if so_no_prazo:
                d = _parse_ddmmaaaa(r.dia)
                if d and (hoje - d).days >= int(prazo):   # vencida -> fora do 'atual'
                    continue
            n += 1
        return n
    except Exception:
        return 0


def pendencia_por_id(pid: int) -> dict:
    """conta/dia/gto/paciente de UMA pendencia. A tela de arquivos resolve o caminho
    da pasta a partir DAQUI, nunca da URL: se o caminho viesse por parametro, daria
    para montar a pasta de qualquer outro paciente."""
    with SessionLocal() as s:
        p = s.get(Pendencia, int(pid))
        if not p:
            return {}
        return {"id": p.id, "conta": p.conta, "dia": p.dia, "gto": p.gto,
                "paciente": p.paciente, "categoria": p.categoria,
                "resolvido": bool(p.resolvido)}


def listar_pendencias(status: str = "abertas", limit: int = 5000) -> list:
    with SessionLocal() as s:
        q = s.query(Pendencia)
        if status == "abertas":
            q = q.filter(Pendencia.resolvido == False)      # noqa: E712
        elif status == "resolvidas":
            q = q.filter(Pendencia.resolvido == True)        # noqa: E712
        rows = q.order_by(Pendencia.resolvido, Pendencia.criado_em.desc()).limit(limit).all()
        
        def _parse(d_str):
            try:
                import datetime
                return datetime.datetime.strptime(d_str, "%d/%m/%Y").date()
            except Exception:
                import datetime
                return datetime.date.min

        out = [{"id": p.id, "conta": p.conta, "dia": p.dia, "gto": p.gto,
                 "paciente": p.paciente, "categoria": p.categoria, "motivo": p.motivo,
                 "criado_em": p.criado_em, "resolvido": p.resolvido,
                 "resolvido_em": p.resolvido_em, "resolvido_por": p.resolvido_por,
                 "obs": p.obs} for p in rows]
        if status == "abertas":
            out.sort(key=lambda x: _parse(x["dia"]))
        return out


def leituras_por_gtos(gtos: list) -> dict:
    """Para cada GTO, O QUE A IA LEU na última execução — evidência p/ a tela de
    pendências ficar EXPLICATIVA (pedido do dono 13/08: "a pendência tem que dizer
    o que a IA leu no pedido, não só o porquê"). Puxa do ExecucaoItem mais recente
    do gto: exames da guia, exames lidos no pedido e o resumo por anexo. Devolve
    {gto: {"exames_gto","exames_lidos","lido"}}."""
    gs = {str(g) for g in (gtos or []) if g}
    if not gs:
        return {}
    out = {}
    with SessionLocal() as s:
        # ordem crescente: o ÚLTIMO de cada gto sobrescreve = a leitura mais recente
        rows = (s.query(ExecucaoItem)
                .filter(ExecucaoItem.gto.in_(gs))
                .order_by(ExecucaoItem.id.asc()).all())
        for it in rows:
            out[str(it.gto)] = {
                "exames_gto": (it.exames_gto or "").strip(),
                "exames_lidos": (it.exames_lidos or "").strip(),
                "lido": (it.paciente_lido or "").strip(),
            }
    return out


def resolver_pendencia(pid: int, username: str, obs: str = None):
    with SessionLocal() as s:
        p = s.get(Pendencia, pid)
        if p:
            p.resolvido = True; p.resolvido_em = _now(); p.resolvido_por = username or "?"
            if obs is not None:
                p.obs = obs
            s.commit()


def reabrir_pendencia(pid: int):
    with SessionLocal() as s:
        p = s.get(Pendencia, pid)
        if p:
            p.resolvido = False; p.resolvido_em = None; p.resolvido_por = None
            s.commit()


def _parse_ddmmaaaa(v):
    try:
        from datetime import date as _date
        p = str(v).split("/")
        return _date(int(p[2]), int(p[1]), int(p[0]))
    except Exception:
        return None


def dias_com_pendencia_aberta(prazo_dias: int = None) -> list:
    """(conta, dia) distintos que têm pendência aberta. Se prazo_dias, filtra só os
    dias dentro da janela (dia do exame >= hoje - prazo_dias) — fora do prazo não
    adianta reprocessar."""
    from datetime import date, timedelta
    try:
        with SessionLocal() as s:
            rows = (s.query(Pendencia.conta, Pendencia.dia)
                    .filter(Pendencia.resolvido == False)      # noqa: E712
                    .distinct().all())
    except Exception:
        return []
    limite = (date.today() - timedelta(days=prazo_dias)) if prazo_dias else None
    out = set()
    for conta, dia in rows:
        if not conta or not dia:
            continue
        if limite:
            d = _parse_ddmmaaaa(dia)
            if d and d < limite:
                continue
        out.add((conta, dia))
    return sorted(out)


class CronState(Base):
    """Estado dos jobs automáticos (linha única id=1)."""
    __tablename__ = "cron_state"
    id = Column(Integer, primary_key=True)
    faturar_last_at = Column(DateTime(timezone=True))
    faturar_last_dia = Column(String(10))
    resumo_fat_last_at = Column(DateTime(timezone=True))
    # DISJUNTOR (22/08): ate quando a fila de retry fica parada por falha GLOBAL
    # (proxy fora, login nao passa). Enquanto pausada, nenhuma guia gasta tentativa.
    retry_pausado_ate = Column(DateTime(timezone=True))
    retry_pausa_motivo = Column(Text)


def cron_marcar_faturar(dia: str):
    with SessionLocal() as s:
        c = s.get(CronState, 1)
        if not c:
            c = CronState(id=1); s.add(c)
        c.faturar_last_at = _now(); c.faturar_last_dia = dia
        s.commit()


def cron_faturar_last_at():
    try:
        with SessionLocal() as s:
            c = s.get(CronState, 1)
            return c.faturar_last_at if c else None
    except Exception:
        return None


def cron_marcar_resumo_fat():
    with SessionLocal() as s:
        c = s.get(CronState, 1)
        if not c:
            c = CronState(id=1); s.add(c)
        c.resumo_fat_last_at = _now()
        s.commit()


def cron_resumo_fat_last_at():
    try:
        with SessionLocal() as s:
            c = s.get(CronState, 1)
            return c.resumo_fat_last_at if c else None
    except Exception:
        return None


class PortalCredencial(Base):
    """Senha do portal RedeUna/OdontoPrev por código de conta (plano).
    Sobrepõe a ODONTOPREV_PASSWORD do ambiente quando cadastrada."""
    __tablename__ = "portal_credenciais"
    conta = Column(String(20), primary_key=True)
    senha = Column(Text)
    atualizado_em = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    atualizado_por = Column(String(60))


# ── Cifra da senha do portal ──────────────────────────────────────────────────
# A senha do convênio ficava em TEXTO PURO no banco: quem tivesse a DATABASE_URL
# tinha, junto, o login das três unidades no OdontoPrev. Com PORTAL_KEY definida,
# grava cifrada (prefixo 'enc:'). Sem ela, o comportamento é o de hoje — texto
# puro — para não quebrar quem ainda não configurou; a leitura aceita os dois
# formatos, então a migração acontece sozinha no próximo salvamento.
_ENC_PREFIXO = "enc:"


def _fernet():
    k = os.environ.get("PORTAL_KEY")
    if not k:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(k.encode() if isinstance(k, str) else k)
    except Exception as e:
        print(f"[db] PORTAL_KEY inválida ({str(e)[:60]}) — senha do portal segue "
              f"em texto puro", flush=True)
        return None


def _cifrar(senha: str) -> str:
    f = _fernet()
    if not f or not senha:
        return senha
    return _ENC_PREFIXO + f.encrypt(senha.encode()).decode()


def _decifrar(valor: str):
    if not valor or not valor.startswith(_ENC_PREFIXO):
        return valor                      # legado em texto puro
    f = _fernet()
    if not f:
        print("[db] senha do portal está cifrada mas PORTAL_KEY não está definida",
              flush=True)
        return None
    try:
        return f.decrypt(valor[len(_ENC_PREFIXO):].encode()).decode()
    except Exception as e:
        print(f"[db] falha ao decifrar senha do portal: {str(e)[:60]}", flush=True)
        return None


def set_portal_senha(conta: str, senha: str, username: str = None):
    conta = (conta or "").strip()
    if not conta:
        raise ValueError("conta obrigatória")
    senha = _cifrar(senha)
    with SessionLocal() as s:
        c = s.get(PortalCredencial, conta)
        if c:
            c.senha = senha; c.atualizado_por = username
        else:
            s.add(PortalCredencial(conta=conta, senha=senha, atualizado_por=username))
        s.commit()


def get_portal_senha(conta: str):
    if not conta:
        return None
    try:
        with SessionLocal() as s:
            c = s.get(PortalCredencial, conta)
            return _decifrar(c.senha) if (c and c.senha) else None
    except Exception:
        return None


def listar_portal_status() -> dict:
    """Por conta: se tem senha cadastrada, quando e por quem (nunca devolve a senha)."""
    try:
        with SessionLocal() as s:
            return {c.conta: {"tem": bool(c.senha), "atualizado_em": c.atualizado_em,
                              "por": c.atualizado_por}
                    for c in s.query(PortalCredencial).all()}
    except Exception:
        return {}


def _exames_visiveis(exames) -> str:
    """Lista de exames como a operadora le: sem tokens internos, com nome amigavel."""
    try:
        from solicitacao_utils import lista_amigavel
        return lista_amigavel(exames)
    except Exception:
        return ", ".join(str(e) for e in (exames or [])
                         if not str(e).startswith("documentacao_"))


def _gto_dia(conta, dia) -> str:
    """Chave sentinela da fila pra um DIA INTEIRO (execucao que abortou). A fila e
    por guia; um aborto nao tem guia nenhuma — nao chegou a decidir nada. Este id
    faz o dia caber na mesma fila, com o mesmo backoff e o mesmo teto."""
    return f"__DIA__{conta}__{dia}"


def registrar_retry_dia(conta, dia, erro) -> bool:
    """Enfileira o DIA INTEIRO pra nova tentativa. Retorna True se e a PRIMEIRA vez
    (pro aviso nao repetir a cada re-tentativa que aborta de novo)."""
    from datetime import timedelta
    _g = _gto_dia(conta, dia)
    with SessionLocal() as s:
        it = (s.query(RetryFila)
              .filter(RetryFila.gto == _g, RetryFila.resolvido == False)  # noqa: E712
              .first())
        if it is not None:
            it.ultimo_erro = str(erro or "")[:300]
            s.commit()
            return False
        s.add(RetryFila(gto=_g, conta=str(conta), dia=str(dia), classe="nosso",
                        tentativas=0, ultimo_erro=str(erro or "")[:300],
                        proximo_em=_now() + timedelta(minutes=retry_backoff_min(0))))
        s.commit()
    return True


def salvar_execucao_falha(dia: str, conta: str, dry_run: bool, erro: str,
                          log_linhas=None) -> int:
    """Registra uma execucao que NAO chegou ao fim. Sem isto, erro em
    rodar_esteira apagava a execucao inteira do historico — a operadora relatava
    "rodei o dia" e nao havia nada para conferir. Nao cria pendencia (nada foi
    decidido); serve para o dia/unidade nao ficar invisivel."""
    with SessionLocal() as s:
        ex = Execucao(dia=dia, conta=conta, dry_run=bool(dry_run),
                      erro=(erro or "")[:4000],
                      log=(chr(10).join(str(l) for l in log_linhas) if log_linhas else None))
        s.add(ex)
        s.commit()
        _id = ex.id
    # FURO FECHADO (22/08): antes o aborto morria aqui — sem pendencia, sem fila e
    # sem ninguem avisado. O dia inteiro simplesmente nao faturava, em silencio.
    # Agora entra na fila (o sistema tenta de novo) e o dono sabe na hora.
    if not dry_run:
        try:
            _primeira = registrar_retry_dia(conta, dia, erro)
            if _primeira:
                import notificador
                notificador.avisar_aborto(dia, conta, erro, execucao_id=_id)
        except Exception as e:
            print(f"[db] aviso de aborto falhou: {e}", flush=True)
    return _id


def salvar_execucao(resumo: dict, log_linhas=None) -> int:
    """Persiste uma execução (resumo do rodar_esteira) + seus itens + backlog.
    log_linhas: log técnico completo, para o histórico sobreviver ao restart."""
    with SessionLocal() as s:
        baix = resumo.get("baixados", 0)
        itens_info = []
        ex = Execucao(
            dia=resumo.get("data"), conta=resumo.get("conta"),
            dry_run=bool(resumo.get("dry_run", True)),
            tempo_total=int(resumo.get("tempo_total", 0)),
            tempo_descoberta=int(resumo.get("tempo_descoberta", 0)),
            tempo_download=int(resumo.get("tempo_ate_download", 0)),
            pendentes=int(resumo.get("pendentes", 0)),
            faturadas=int(resumo.get("anexado_ok", 0)),
            nao_faturadas=int(baix - resumo.get("anexado_ok", 0)),
            m_download=int(resumo.get("m_download", 0) or 0),
            k_leitura=int(resumo.get("k_leitura", 0) or 0),
            log=(chr(10).join(str(l) for l in log_linhas) if log_linhas else None),
            gemini_chamadas=int((resumo.get("gemini_tokens") or {}).get("chamadas", 0) or 0),
            gemini_tokens_in=int((resumo.get("gemini_tokens") or {}).get("in", 0) or 0),
            gemini_tokens_out=int((resumo.get("gemini_tokens") or {}).get("out", 0) or 0),
        )
        for x in resumo.get("decisoes", []):
            g = x.get("gemini") or {}
            faturado = x.get("anexado") == "OK"
            cat = x.get("categoria")
            if cat == "ja_anexada":
                # Faturada, mas NAO por nos. O relatorio tem de dizer isso: antes
                # aparecia como "auto"/faturada, igual a uma guia que o robo anexou.
                motivo = (x.get("gemini") or {}).get("motivo") or ""
            elif faturado:
                motivo = ""
            elif x.get("anexar_erro"):
                # A documentação estava OK — quem falhou foi a ANEXAÇÃO. Antes esse
                # erro nunca era lido: a pendência saía com motivo vazio (categoria
                # "justificativa") ou com o texto POSITIVO do Gemini (categoria
                # "auto"), fazendo parecer culpa da clínica. Agora diz a verdade.
                motivo = f"Documentação OK, mas a anexação falhou: {x['anexar_erro']}"
            elif cat == "sem_solicitacao":
                motivo = g.get("motivo") or x.get("erro") or "Sem solicitação e sem justificativa (campo 49 vazio)"
                # A esteira ja sabe se o laudo tambem falta; sem isto o segundo
                # bloqueio some do painel (caso EVELYN).
                motivo = motivo_com_laudo_faltando(motivo, bool(x.get("falta_laudo")))
            elif cat == "sem_laudo" and x.get("falta_tele"):
                # Doc ortodôntica com a panorâmica, mas sem o traçado. Motivo específico
                # (o classificador reconhece como 'esperando_tele' → Radiologista).
                motivo = ("Documentação ortodôntica com a panorâmica anexada, mas SEM o "
                          "LAUDO da telerradiografia (traçado cefalométrico). O robô anexa "
                          "sozinho assim que o traçado sair no PRORADIS — cobrar a emissão "
                          "do traçado.")
            elif cat == "sem_laudo":
                motivo = "Solicitação OK, mas falta o LAUDO válido no PRORADIS (anexos sem laudo, ou laudo veio em branco/não pronto)"
            elif cat == "justificativa":
                motivo = ""  # foi faturado por justificativa (se anexado)
            else:
                motivo = g.get("motivo") or x.get("erro") or (
                    "NÃO FATUROU: guia enviada para REVISÃO HUMANA — o robô não "
                    "conseguiu confirmar automaticamente a solicitação/laudo desta "
                    "guia (ficou ambígua). O QUE FAZER: abrir a execução do dia, "
                    "conferir a solicitação e a guia no prontuário e decidir.")
            ex.itens.append(ExecucaoItem(
                gto=str(x.get("gto")), paciente=x.get("paciente"),
                categoria=cat, faturado=faturado, motivo=motivo,
                solicitacao=x.get("solicitacao"),
                # nomes que a OPERADORA le, nao os tokens internos. O marcador
                # 'documentacao_completa' (que separa a Completa da Controle)
                # vazava para a coluna Exames da tela de faturadas.
                exames_gto=_exames_visiveis(x.get("gto_exames")),
                exames_lidos=", ".join(str(e) for e in (g.get("exames_lidos") or [])),
                n_arquivos=len(x.get("laudo_imgs") or []) + (1 if x.get("solicitacao") else 0),
                arquivos_plano=", ".join(x.get("arquivos_anexados")
                                         or x.get("laudo_imgs") or [])[:4000] or None,
                excluidos=", ".join(x.get("laudos_excluidos") or [])[:2000] or None,
                funil=(lambda f: (f"prontuario={f.get('prontuario')} cand={f.get('candidatos')} "
                                  f"descartados={f.get('descartados')} conv={f.get('convertidos')}")
                       if f else None)(x.get("funil")),
                # quando NAO casou, grava o que foi lido em CADA anexo — e o dado
                # que permite dizer se e cadastro divergente ou rigor da regra
                paciente_lido=((g.get("paciente_lido") or x.get("nomes_lidos") or None)
                               if not faturado else (g.get("paciente_lido") or None)),
                data_exame_real=x.get("data_exame_real"),
            ))
            itens_info.append((str(x.get("gto")), x.get("paciente"), cat, motivo, faturado))
        s.add(ex)
        try:
            s.commit()
        except Exception as e:
            # NUNCA perder a execucao inteira por causa de um campo. Regrava sem os
            # textos longos, para que o historico e os motivos existam de qualquer jeito.
            s.rollback()
            print(f"[db] regravando execucao sem campos longos: {str(e)[:120]}", flush=True)
            for it in ex.itens:
                it.paciente_lido = (it.paciente_lido or "")[:150] or None
                it.arquivos_plano = (it.arquivos_plano or "")[:150] or None
                it.excluidos = (it.excluidos or "")[:150] or None
                it.funil = (it.funil or "")[:110] or None
                it.motivo = (it.motivo or "")[:900] or None
            s.add(ex)
            s.commit()
        # backlog de revisão humana — só em execução REAL (dry_run não gera pendência)
        if not bool(resumo.get("dry_run", True)):
            try:
                _sync_pendencias(s, resumo.get("conta"), resumo.get("data"), ex.id, itens_info)
            except Exception as e:
                print(f"[db] sync pendencias falhou: {e}", flush=True)
            # FILA DE RETRY (Fase 3): faturado sai da fila; falha NOSSA entra (pra o
            # loop re-tentar sozinho — regra do dono 22/08: falha de sistema o sistema
            # resolve). Externo e Conferencia nao entram (nao retry cego).
            _novas_nossas = []
            try:
                for _gto, _pac, _cat, _mot, _fat in itens_info:
                    if _fat:
                        resolver_retry(_gto)
                    elif deve_entrar_no_retry(_mot, _cat):
                        # so conta como NOVA se ainda nao estava na fila — assim uma
                        # rodada de retry que falha de novo nao re-avisa o dono.
                        _nova = not retry_na_fila(_gto)
                        registrar_retry(_gto, resumo.get("conta"), resumo.get("data"),
                                        _mot, _cat, paciente=_pac)
                        if _nova and deve_avisar_na_rodada(_gto):
                            _novas_nossas.append({"gto": _gto, "paciente": _pac,
                                                  "motivo": _mot})
            except Exception as e:
                print(f"[db] sync retry_fila falhou: {e}", flush=True)
            # A rodada terminou: UMA mensagem com as falhas nossas novas. O operador
            # nao ve nenhuma delas (eh_pendencia_front); quem precisa saber e o dono.
            try:
                if _novas_nossas:
                    import notificador
                    notificador.avisar_falhas_da_rodada(
                        resumo.get("data"), resumo.get("conta"), _novas_nossas)
            except Exception as e:
                print(f"[db] aviso whatsapp falhou: {e}", flush=True)
            # a rodada chegou ao fim: se este dia/unidade estava marcado como ABORTADO,
            # deixou de estar (nao adianta re-rodar o dia inteiro de novo).
            try:
                resolver_retry(_gto_dia(resumo.get("conta"), resumo.get("data")))
            except Exception:
                pass
            # AVISOS 'exame sem guia' — laudo pronto sem GTO (particular/esquecido)
            try:
                avisos_info = []
                for x in resumo.get("decisoes", []):
                    for lg in (x.get("laudos_sem_guia") or []):
                        avisos_info.append({
                            "accession": lg.get("accession"), "exame": lg.get("exame"),
                            "gto": x.get("gto"), "paciente": x.get("paciente")})
                if avisos_info:
                    _sync_avisos(s, resumo.get("conta"), resumo.get("data"), ex.id, avisos_info)
            except Exception as e:
                print(f"[db] sync avisos falhou: {e}", flush=True)
        return ex.id


# ── Pendências de um dia, agrupadas por QUEM RESOLVE ─────────────────────────
# O relatório do dia já dizia o que não faturou e por quê. Faltava a pergunta que
# a operadora realmente faz: "em qual destas eu preciso mexer?". Uma lista de 10
# nomes misturados esconde que 7 se resolvem sozinhas quando o laudo sair e só 3
# dependem de alguém. Agrupar por responsável é o que transforma relatório em
# tarefa. Pedido do dono, 30/07.
#
# A ordem importa: o primeiro padrão que casar vence, então o mais específico vem
# antes. Cada grupo carrega a AÇÃO — quem lê não precisa deduzir o que fazer.
_GRUPOS_PENDENCIA = [
    # PRENOME MAL LIDO (23/08): todos os sobrenomes batem e so o primeiro nome
    # difere — ANETE ANDRADE DE MATTOS lida como 'Plunet Andrade de Mattos'. E
    # leitura, nao documento de terceiro. Vem ANTES de nome_nao_bate, que mandaria
    # "solicite a clinica o pedido correto" de um pedido que ja esta la.
    # DIVERGENCIA DE LEITURA DO NOME -> CLINICA (regra do dono, 23/08). A IA LEU o
    # documento; a leitura simplesmente nao casa com o nome da guia. Isso e falha
    # tecnica de leitura/casamento, e re-tentar NUNCA faz um nome divergente passar
    # a casar — segurar na fila tecnica prende a guia para sempre. Quem resolve e a
    # CLINICA, anexando um pedido com o nome correto/legivel. O botao de confirmar
    # continua na tela como atalho para quem CONFERIR o papel e reconhecer o
    # paciente; o dono da pendencia, porem, e a clinica.
    ("prenome_mal_lido", r"TODOS OS SOBRENOMES BATEM|erro de leitura do prenome",
     "Clínica", "A IA leu o pedido, mas o nome não casou com o da guia — todos os "
     "sobrenomes batem e só o primeiro nome saiu diferente, o que tem cara de leitura "
     "ruim (letra de médico, carimbo borrado). Pedir à clínica um pedido em que o nome "
     "do paciente esteja legível. Se alguém abrir a solicitação e reconhecer o "
     "paciente, dá para liberar pelo botão — nunca anexar sem conferir."),
    # MODELO sem render (22/08): a guia de MODELO nao tem laudo por definicao — o
    # entregavel e o render 3D. Vem ANTES de sem_entregavel, que mandaria "cobrar a
    # emissao do laudo" de um exame que nunca tera laudo.
    ("modelo_sem_render", r"render 3D do MODELO|render do modelo",
     "Radiologista", "Guia de MODELO: não tem laudo — o entregável é o render 3D "
     "do escaneamento. Ele ainda não foi gerado no PRORADIS. O robô anexa sozinho "
     "assim que sair; cobrar a geração do modelo."),
    # ANALISE CEFALOMETRICA faltando (22/08, caso JOSEANE): a tele TEM laudo, mas o
    # pedido nomeia uma analise (Ricketts/USP/Tweed...) que nao esta no documento.
    # Vem PRIMEIRO porque o texto contem "falta o LAUDO", que casaria em falta_laudo
    # e viraria uma cobranca generica, sem dizer QUAL analise pedir.
    ("esperando_analise", r"LAUDO da an[áa]lise|laudo da analise",
     "Radiologista", "A telerradiografia tem laudo, mas falta a ANÁLISE que o pedido "
     "nomeia (ex.: Ricketts). O robô anexa sozinho assim que ela sair — cobrar a "
     "emissão dessa análise específica."),
    ("sem_entregavel", r"n[ãa]o h[áa] laudo nem imagem|ainda n[ãa]o tem entreg[áa]vel",
     "Radiologista", "Exame registrado sem laudo E sem imagem — não há o que anexar. "
     "Cobrar a emissão."),
    # TELE (traçado cefalométrico) faltando numa documentação ortodôntica: vem ANTES
    # de falta_laudo (mais específico). A guia TEM a panorâmica; falta só o laudo da
    # telerradiografia. Causa dos 30 faturados-sem-tele (conferência RedeUna 01/08).
    ("esperando_tele",
     r"LAUDO da telerradiografia|tra[çc]ado cefalom|laudo da tele|falta laudo tele",
     "Radiologista", "Documentação ortodôntica com a panorâmica anexada, mas SEM o "
     "laudo da telerradiografia (traçado cefalométrico). O robô anexa sozinho assim "
     "que o traçado sair no PRORADIS — cobrar a emissão do traçado."),
    ("falta_laudo", r"falta o LAUDO|sem laudo|laudo veio em branco|laudo.*n[ãa]o pronto",
     "Radiologista", "O robô anexa sozinho assim que o laudo sair. Só cobrar."),
    # PEDIDO ILEGÍVEL (caso MARIA CLARA): o robô não leu a caligrafia dos exames.
    # NÃO é culpa da clínica (o pedido existe e pode cobrir) — é leitura nossa que
    # falhou. Vem ANTES de pedido_nao_cobre. Resolução: conferir e anexar à mão.
    ("pedido_ilegivel",
     r"caligrafia do pedido.{0,40}ileg[íi]vel|n[ãa]o conseguiu ler os exames escritos",
     "Conferência", "A letra do pedido do dentista não foi lida pelo robô — não é "
     "falta de pedido. Conferir o pedido no prontuário e anexar à mão."),
    # CARINA (28/07): HÁ documento no nome do paciente, mas a solicitação está mal-lida
    # ou pede exame diferente — não é falta de pedido nem nome errado. Caía em "Outros"
    # (fallback sem ação); a reconciliação 13/08 achou 9 guias assim.
    ("solic_nao_confirmada",
     r"solicita[çc][ãa]o do dentista n[ãa]o p[ôo]de ser confirmada",
     "Conferência", "Há documento no nome do paciente, mas a solicitação está mal-lida "
     "ou pede exame diferente do que a guia autoriza. Conferir o pedido no prontuário "
     "e, se cobrir, anexar à mão."),
    ("pedido_nao_cobre", r"n[ãa]o cobre tudo que a guia autoriza|FALTA no pedido",
     "Clínica", "Pedir à clínica um pedido que inclua o exame que falta."),
    # BLOQUEIO DUPLO (23/08, caso EVELYN 196330383): falta o pedido E o nosso laudo.
    # Vem ANTES de sem_pedido porque o texto contem o motivo do pedido inteiro e
    # casaria la, saindo como "Clinica" — mandando a operacao cobrar so metade.
    ("sem_pedido_e_laudo", r"NÃO SAI SÓ COM O PEDIDO|NAO SAI SO COM O PEDIDO",
     "Clínica + Radiologista",
     "Faltam DUAS coisas: o pedido do dentista (cobrar da clínica) e o laudo do "
     "exame (cobrar a emissão no PRORADIS). Cobrar os dois em paralelo — a guia só "
     "fatura quando os dois chegarem."),
    ("sem_pedido", r"nenhum pedido do dentista|n[ãa]o h[áa] nenhum pedido|sem anexo candidato"
     r"|Sem solicita[çc][ãa]o e sem justificativa",
     "Clínica", "Pedir à clínica que anexe o pedido no prontuário."),
    # Pedido validado mas com data velha que o robô não conseguiu ajustar
    # (caso ESTER, 27/07 — caía em "Outros" sem dono)
    ("data_vencida", r"data vencida",
     "Conferência", "O pedido é do paciente e cobre a guia, mas está com data "
     "antiga e o robô não conseguiu ajustar. Conferir na Revisão e anexar "
     "manualmente (ou pedir pedido novo)."),
    # DOCUMENTO DE OUTRA PESSOA. Responsavel virou CLINICA em 23/08, depois de
    # verificar caso a caso: o prontuario da HOSANA BARRETO DOS SANTOS tinha pedido
    # de 'GLADYS FREITAS DOS SANTOS' — o proprio texto diz "Para Sr(a): GLADYS
    # FREITAS DOS SANTOS", nascimento 12/11/1972. Recusa CORRETA (caso JOCASTA).
    # Marcada como 'Nos', a guia sumia do painel e ficava presa no retry para
    # sempre, re-tentando o que nunca vai mudar sozinho. O que falta e a CLINICA
    # anexar o pedido desta paciente. O caso de erro de leitura do prenome saiu
    # daqui para `prenome_mal_lido` (Conferencia), que vem antes na tabela.
    ("nome_nao_bate", r"nenhum documento do prontu[áa]rio est[áa] no nome"
     r"|nenhum anexo com paciente compat",
     "Clínica", "O prontuário só tem documento de OUTRO paciente — o pedido desta "
     "pessoa não está lá. Pedir à clínica que anexe o pedido correto. Nunca anexar "
     "documento de terceiro: gera glosa."),
    ("guia_ilegivel", r"n[ãa]o conseguiu ler quais exames a guia autoriza|GTO ileg[íi]vel"
     r"|sem exames de refer[êe]ncia",
     "Nós", "Não lemos o que a guia autoriza. Abrir a guia no portal e conferir."),
    ("anexacao", r"anexa[çc][ãa]o falhou|n[ãa]o sobrou nenhum laudo|upload",
     "Nós", "A decisão passou e a anexação foi barrada. Conferir se o exame é do convênio."),
    # Falha do robô/leitura (ex.: "gemini: ..." — caso SOPHIA, 27/07): é NOSSA,
    # vai para a fila técnica, não para a operação
    ("falha_tecnica", r"\bgemini\s*:|falha t[ée]cnica|leitura indispon[íi]vel"
     r"|leitura autom[áa]tica ficou indispon|cr[ée]ditos da API",
     "Nós", "Falha nossa (robô/leitura), não do documento. Rodar o dia de novo "
     "depois da correção."),
    ("paciente_nao_achado", r"n[ãa]o foi encontrado no PRORADIS|paciente da guia n[ãa]o foi"
     r"|n[ãa]o encontrado no cadastro do PRORADIS",
     "Cadastro", "Procurar no PRORADIS pelo primeiro nome e conferir se o cadastro "
     "bate com o da guia."),
    # Exame achado em MAIS DE UM dia vizinho da guia (janela ±7) — caía em "Outros"
    ("multi_dia", r"exame em mais de um dia|mais de um dia pr[óo]ximo",
     "Conferência", "O exame aparece em mais de um dia próximo ao da guia. "
     "Dizer qual é o dia certo e rodar esse dia."),
    ("homonimo", r"mais de um paciente|hom[ôo]nimo",
     "Conferência", "Dois pacientes com o mesmo nome. Dizer qual é o certo."),
    # Genéricos que caíam em "Outros" (reconciliação 13/08): revisão humana (6) e a
    # nota de data ajustada (1) que sobrava como pendência.
    ("revisao_humana", r"revis[ãa]o humana",
     "Conferência", "O robô marcou para revisão humana. Abrir a execução, conferir a "
     "solicitação e a guia e decidir."),
    ("data_ajustada", r"[Dd]ata ajustada automaticamente",
     "Conferência", "O robô ajustou a data da solicitação automaticamente. Conferir "
     "se a guia faturou ou se precisa reprocessar o dia."),
]


_SUFIXO_LAUDO = (
    " ⚠ ATENÇÃO — ESTA GUIA NÃO SAI SÓ COM O PEDIDO: falta também o LAUDO do "
    "exame, que é NOSSO (radiologista RadioBras). Cobrar a emissão do laudo no "
    "PRORADIS em paralelo — senão a clínica anexa o pedido e a guia continua parada.")


def nomes_lidos_resumo(lido: str) -> list:
    """Os nomes que a IA leu nos anexos, em ordem, sem repetir.

    Serve para a operadora ver O QUE ESTA ESCRITO no documento antes de clicar em
    "Confirmei que e o paciente" — clique irreversivel. Escondido num <details>
    colapsado, o nome nao chegava a ela: a HOSANA (196346585) tem um pedido que diz
    "Para Sr(a): GLADYS FREITAS DOS SANTOS" e mesmo assim o botao aparecia limpo.
    """
    import re as _re
    out = []
    for m in _re.finditer(r"paciente='([^']*)'", str(lido or "")):
        nome = (m.group(1) or "").strip()
        if nome and nome not in out:
            out.append(nome)
    return out


def motivo_com_laudo_faltando(motivo: str, falta_laudo: bool) -> str:
    """Junta o segundo bloqueio ao texto da pendencia.

    A cadeia de categorizacao da esteira e excludente: quando faltam o pedido E o
    laudo, `sem_solicitacao` vence e o laudo desaparece do motivo — embora o log da
    mesma execucao ja tivesse escrito "FALTA: LAUDO+SOLICITACAO" (caso EVELYN,
    196330383, 4 rodadas). A operacao cobrava so a clinica e a guia voltava parada.
    """
    if not falta_laudo:
        return motivo or ""
    return ((motivo or "").rstrip() + _SUFIXO_LAUDO).strip()


def classificar_pendencia(motivo: str, categoria: str = "") -> tuple:
    """(chave, responsável, ação) de uma pendência, a partir do motivo escrito."""
    import re as _re
    m = str(motivo or "")
    for chave, padrao, quem, acao in _GRUPOS_PENDENCIA:
        if _re.search(padrao, m, _re.I):
            return chave, quem, acao
    return ("outros", "Conferência",
            "Motivo fora dos padrões conhecidos — abrir a execução e ler o log técnico.")


_NOSSO = "Nós"   # responsavel cujas pendencias vao para a FILA TECNICA


# ── Classe de RETRY (Fase 0) ─────────────────────────────────────────────────
# Diz se uma falha e infra transitoria (o loop retenta sozinho), esperando algo
# EXTERNO (pendencia, sem retry) ou de LOGICA (conserto/leitura, NAO retry cego).
# Regra: o loop de retry SO retenta 'transitorio'.
_TRANSITORIO_RE = __import__("re").compile(
    r"gemini\s*:|UNAVAILABLE|time.?out|timed out|net::|ERR_|tunnel|"
    r"context was destroyed|translate host|throttl|rate.?limit|TE-BFF-GTO|"
    r"falha t[ée]cnica|leitura indispon|pacientes com o nome.{0,40}n[ãa]o foi poss|"
    # Leitura que "nao retornou nada" / "falha temporaria da leitura": o proprio
    # relatorio ja manda "reprocessar o dia (falha nossa)" — entao o loop TEM que
    # retentar (antes caia em 'logica' e ficava parado). Trace 17/08: 503 por-candidato
    # virava leitura vazia -> nao retentava. So casa a frase que o codigo emite; nao
    # pega 'sem pedido'/'nao cobre' (que dizem 'pedir a clinica', nunca 'reprocessar').
    r"leitura.{0,40}n[ãa]o retornou|falha tempor[áa]ria|"
    # QUOTA do Gemini (22/08): "a leitura automatica ficou indisponivel: os
    # creditos da API acabaram". So casava por categoria='erro'; pelo TEXTO caia
    # em 'outros' -> Conferencia -> aparecia PRO CLIENTE. Regra do dono: falha de
    # sistema nunca chega na cliente. O texto passa a se defender sozinho, sem
    # depender de a categoria sobreviver a releitura/relatorio/exportacao.
    r"leitura autom[áa]tica ficou indispon|cr[ée]ditos da API|"
    # ANEXAÇÃO falhou por INFRA (17/08): o JWT do OdontoPrev expira em rodada longa e
    # o anexador, sem conseguir CONTAR os anexos, NÃO envia (anti-duplicação). Rodada
    # nova com token fresco resolve. NÃO casa 'laudo de outro exame' (dado -> conferência).
    r"Jwt is expired|jwt.{0,6}expir|n[ãa]o consegui ler quantos anexos",
    __import__("re").I)
# NOTA: 'homônimo/mais de um paciente' saiu do transitório (13/08). O caso "mesmo dia"
# (lado analítico, 195904169) NÃO se resolve com retry — é Conferência (vai pro front).
# A ALESSANDRA ("N pacientes com o nome ... não foi possível") segue transitório pelo
# padrão 'pacientes com o nome ... não foi poss' (o nascimento desempata num re-run).


def classe_retry(motivo: str, categoria: str = "") -> str:
    """'transitorio' | 'externo' | 'logica'. O loop de retry so retenta o
    transitorio. Transitorio = infra (gemini/rede/throttle) OU homonimo (o
    nascimento, agora com retry no fetch, desempata numa boa rodada). Externo =
    responsavel Radiologista/Clinica/Cadastro. Resto = logica (nome/ilegivel/
    revisao/desconhecido) — precisa conserto ou humano, nunca retry cego."""
    m = str(motivo or "")
    if _TRANSITORIO_RE.search(m):
        return "transitorio"
    _chave, quem, _acao = classificar_pendencia(m, categoria)
    # RESPONSAVEL COMPOSTO (23/08): 'Clinica + Radiologista' nao casava na tupla e
    # caia em 'logica'. A EVETLYN — a guia de bloqueio DUPLO, a mais travada da fila
    # — virava tipo 'conferir' (sem ter documento para conferir) e ia para o FIM do
    # relatorio, atras das que dependem de um lado so. Se QUALQUER responsavel for
    # externo, a guia espera terceiro: compor responsaveis nao muda a natureza da
    # espera.
    if any(_x in str(quem or "") for _x in ("Radiologista", "Clínica", "Cadastro")):
        return "externo"
    return "logica"


_CONECTIVOS = {"DE", "DA", "DO", "DAS", "DOS", "E"}


def _tokens_nome(t) -> list:
    """Tokens significativos do nome: sem acento, maiusculo, sem conectivos."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).upper()
    t = __import__("re").sub(r"[^A-Z ]+", " ", t)
    return [x for x in t.split() if x and x not in _CONECTIVOS]


def so_o_prenome_difere(nome_guia, nome_lido) -> bool:
    """Todos os SOBRENOMES sao identicos e so o PRENOME difere?

    Serve para separar duas coisas que hoje caem no mesmo balde `nome_nao_bate`:

      A) leitura do prenome falhou — ANETE ANDRADE DE MATTOS lido como
         'Plunet Andrade de Mattos'; CASSIANA DOS SANTOS NASCIMENTO lido como
         'Camara dos Santos Nascimento'. Precisa de OLHO HUMANO (Conferencia).
      B) documento de OUTRA PESSOA — HOSANA BARRETO DOS SANTOS com pedido de
         'GLADYS FREITAS DOS SANTOS' (verificado: o texto diz "Para Sr(a): GLADYS
         FREITAS DOS SANTOS", nascimento 12/11/1972). Recusa CORRETA; o que falta e
         a clinica anexar o pedido desta paciente.

    Exige 2+ sobrenomes iguais: com um so, 'JOAO SILVA' x 'PEDRO SILVA' passariam e
    metade do Brasil casaria.

    ISTO NAO AFROUXA O GATE DE IDENTIDADE. Nenhum documento passa a ser aceito por
    causa desta funcao — ela so decide o TEXTO e o DONO da pendencia."""
    a, b = _tokens_nome(nome_guia), _tokens_nome(nome_lido)
    if len(a) < 3 or len(b) < 3:
        return False                  # precisa de prenome + 2 sobrenomes
    if a[1:] != b[1:]:
        return False                  # algum sobrenome difere -> outra pessoa
    return a[0] != b[0]               # iguais em tudo = nao e caso desta regra


# Chaves que, mesmo com categoria 'erro', NAO sao falha nossa: o texto ja nomeia
# uma causa que nenhuma re-tentativa resolve.
_ERRO_MAS_DE_TERCEIRO = ("paciente_nao_achado",)


def eh_nosso(motivo: str, categoria: str = "") -> bool:
    """A falha e NOSSA (tecnica)? Regra do dono (22/08/26): falha de sistema NAO e
    pendencia do painel da RadioBras — ela sai da tela do operador, entra no loop de
    retry e e notificada ao dono no WhatsApp.

    Nossa = transitorio (infra) OU responsavel 'Nos' (nome_nao_bate, guia_ilegivel,
    anexacao, falha_tecnica) OU categoria 'erro' — a esteira marca 'erro' quando o
    _decidir falhou por Gemini/anexos, e nesse caso o texto pode nao casar regex
    nenhuma e cair em 'outros'; a categoria sozinha ja prova a culpa (era o furo
    apontado no desenho de 02/08).

    NAO e nossa a Conferencia: ali falta OLHO HUMANO no documento, nao conserto de
    codigo — esconder do painel seria sumir com trabalho real da operacao."""
    if str(categoria or "").strip().lower() == "erro":
        # ...MAS o atalho nao pode passar por cima de um texto que identifica a causa
        # com todas as letras (23/08, caso MARIA DE FATIMA LAMOEDO 196370003). Ela
        # dizia "paciente nao encontrado no cadastro do PRORADIS" e mesmo assim
        # sumia do painel e queimava 6 tentativas de retry. Nenhuma re-tentativa faz
        # o paciente aparecer: ele esta cadastrado com OUTRO nome — foi o caso do
        # VALDEMIR, que o PRORADIS traz como 'VALDEMIR DOS SANTOS PEREIRA' e a guia
        # chama de 'DOS ANJOS'. Quem resolve e o cadastro.
        # Homonimo NAO entra nesta lista de proposito: com 2+ cards o nascimento
        # desempata num re-run (caso ALESSANDRA), entao ali insistir funciona.
        return classificar_pendencia(motivo, categoria)[0] not in _ERRO_MAS_DE_TERCEIRO
    if classe_retry(motivo, categoria) == "transitorio":
        return True
    return classificar_pendencia(motivo, categoria)[1] == _NOSSO


# ── DISJUNTOR: falha GLOBAL x falha da GUIA (22/08) ─────────────────────────
# O incidente: a banda do proxy acabou as 08:31 e as 13 guias do dia 18/08 gastaram
# as 6 tentativas contra o mesmo proxy morto — nenhuma por causa propria. Esgotaram,
# mandaram 13 mensagens em 2 minutos e sairam do loop: quando o proxy voltou, nenhuma
# voltou sozinha. O loop tratava "esta guia falhou" e "o mundo caiu" como a mesma
# coisa. Assinaturas de APAGAO: proxy fora e login que nao passa — as duas afetam
# TODA guia igualmente, entao re-tentar guia por guia so queima orcamento.
# NAO entram aqui: gemini 503, JWT expirado e timeout — esses sao por-guia e o retry
# normal resolve (a rodada seguinte pega token novo).
_GLOBAL_RE = __import__("re").compile(
    r"ProxyError|Max retries exceeded|Cannot connect to proxy|"
    r"n[ãa]o foi poss[íi]vel conectar ao OdontoPrev pelo proxy|"
    r"proxy.{0,40}403|403.{0,40}proxy|"
    r"falha no login|Falha no login",
    __import__("re").I)

# Menos que isto nao pausa a fila: uma guia sozinha com erro de proxy pode ser
# hiccup dela, e pausar tudo por uma seria pior que o problema.
MIN_PARA_APAGAO = 2


def eh_falha_global(motivo) -> bool:
    """A falha e do MUNDO (proxy fora, login nao passa), nao desta guia? Nesse caso
    re-tentar esta guia especificamente nao faz o menor sentido — a proxima vai bater
    no mesmo muro."""
    return bool(_GLOBAL_RE.search(str(motivo or "")))


def rodada_foi_apagao(itens) -> bool:
    """A rodada inteira caiu por falha global? Exige TRES coisas:
      1. ninguem faturou — se UMA passou, a infra estava de pe e o resto e por-guia;
      2. TODA falha tem assinatura global — falha mista significa mundo de pe;
      3. pelo menos MIN_PARA_APAGAO falhas — uma andorinha so nao faz apagao.
    So entao vale devolver as tentativas e pausar a fila."""
    itens = [i for i in (itens or []) if i]
    if not itens:
        return False
    if any(i.get("faturado") for i in itens):
        return False
    falhas = [i for i in itens if not i.get("faturado")]
    if len(falhas) < MIN_PARA_APAGAO:
        return False
    return all(eh_falha_global(i.get("motivo")) for i in falhas)


def deve_entrar_no_retry(motivo: str, categoria: str = "") -> bool:
    """O loop faz 'try again' em TUDO que e nosso (regra do dono 22/08), com o mesmo
    teto do transitorio. Antes so o transitorio entrava e as tres logicas nossas
    (nome_nao_bate, guia_ilegivel, anexacao) ficavam paradas esperando humano — mas a
    auditoria de 17/08 provou que boa parte delas era 503 intermitente disfarcado, e
    re-ler resolvia. O retry RE-LE o documento; nunca afrouxa a trava de identidade
    (JOCASTA continua sendo recusa correta). Externo e Conferencia seguem fora."""
    return eh_nosso(motivo, categoria)


# Loop de retry do transitorio: teto de tentativas + backoff. A 2a tentativa (1o
# retry) e IMEDIATA (regra do dono 17/08: o que so depende de reprocessar nao espera
# 15min). Depois escala pra dar tempo ao 503/throttle limpar sem estourar o teto em
# segundos: imediato -> imediato -> 5m -> 20m -> 1h -> 4h. Teto 6 -> janela ~5.5h.
MAX_RETRIES_TRANSITORIO = 6
_BACKOFF_MIN = [0, 0, 5, 20, 60, 240]


def retry_backoff_min(tentativa: int) -> int:
    """Minutos ate o proximo retry da tentativa N (0-based). As 2 primeiras sao
    imediatas (0min); depois escala e satura em 4h. A maioria dos transitorios
    (gemini 503) some em minutos -> a 1a imediata ja pega quase tudo; as espacadas
    pegam queda longa (PRORADIS/proxy fora)."""
    i = max(0, int(tentativa))
    return _BACKOFF_MIN[i] if i < len(_BACKOFF_MIN) else _BACKOFF_MIN[-1]


# Classes que o loop re-tenta. 'nosso' (22/08) e a logica NOSSA — nome_nao_bate,
# guia_ilegivel, anexacao: falha de sistema, entao o sistema tenta de novo. A logica
# de CONFERENCIA (pedido ilegivel, homonimo, revisao humana) segue fora: ali falta
# olho humano no documento, e retry cego so gasta quota do Gemini.
_CLASSES_RETENTAVEIS = ("transitorio", "nosso")


def deve_retentar(classe: str, tentativas: int) -> bool:
    """O loop retenta o que e NOSSO ('transitorio' e 'nosso'), ate o teto. Externo e
    logica-de-conferencia nunca (externo espera terceiro; conferencia precisa de
    humano — retry cego so gasta recurso)."""
    return classe in _CLASSES_RETENTAVEIS and int(tentativas) < MAX_RETRIES_TRANSITORIO


def classe_efetiva(motivo: str, categoria: str = "", tentativas: int = 0) -> str:
    """A etiqueta HONESTA, agora com ESTADO (o furo que o dono achou 13/08): um
    'transitorio' que JA falhou o teto de vezes NAO pode continuar se anunciando
    como 'nossa, auto-recuperavel' — ele vira 'esgotado' (nossa, o retry nao
    resolveu -> investigar, nao e mais retry cego). `classe_retry` sozinha e
    stateless (olha so o texto de UMA rodada) e por isso rotulava de 'transitorio'
    algo que ja provou nao se recuperar (195831154 falhou a leitura 4x seguidas).
    'externo'/'logica' independem de tentativas (nunca foram retentaveis)."""
    base = classe_retry(motivo, categoria)
    # 22/08: a logica NOSSA passou a entrar no loop, entao ela tambem pode ESGOTAR.
    # Antes so o transitorio esgotava; nome_nao_bate ficava 'logica' pra sempre.
    if eh_nosso(motivo, categoria) and int(tentativas or 0) >= MAX_RETRIES_TRANSITORIO:
        return "esgotado"
    return base


class RetryFila(Base):
    """Fila de retry do transitorio (Fase 3): guias que falharam por INFRA e devem
    ser re-tentadas sozinhas, com backoff, ate recuperar ou esgotar o teto."""
    __tablename__ = "retry_fila"
    id = Column(Integer, primary_key=True)
    gto = Column(String(30), index=True)
    conta = Column(String(20))
    dia = Column(String(12))
    classe = Column(String(20), default="transitorio")
    tentativas = Column(Integer, default=0)
    proximo_em = Column(DateTime(timezone=True), index=True)
    paciente = Column(String(120))   # so p/ o aviso ao dono dizer QUEM, nao e chave
    ultimo_erro = Column(Text)
    resolvido = Column(Boolean, default=False)  # recuperou OU esgotou o teto
    criado_em = Column(DateTime(timezone=True), default=_now)
    atualizado_em = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class ConfirmacaoNome(Base):
    """SINAL VERDE HUMANO (feature 13/08): o usuário abriu uma pendência de
    ilegível/nome-não-bate, conferiu que a solicitação É do paciente e confirmou.
    A esteira lê isto e libera a trava do nome/cobertura SÓ pra esses gtos — o
    laudo continua obrigatório. Reversível (desconfirmar apaga a linha)."""
    __tablename__ = "confirmacoes_nome"
    id = Column(Integer, primary_key=True)
    gto = Column(String(30), unique=True, index=True)
    conta = Column(String(20))
    dia = Column(String(12))
    quem = Column(String(60))       # username que confirmou (a responsabilidade é dele)
    criado_em = Column(DateTime(timezone=True), default=_now)


def confirmar_nome(gto, conta, dia, quem) -> bool:
    """Registra o sinal verde do humano p/ uma guia. Idempotente (1 por gto)."""
    with SessionLocal() as s:
        ja = s.query(ConfirmacaoNome).filter(ConfirmacaoNome.gto == str(gto)).first()
        if ja is None:
            s.add(ConfirmacaoNome(gto=str(gto), conta=str(conta), dia=str(dia),
                                  quem=str(quem or "?")[:60]))
            s.commit()
    return True


def desconfirmar_nome(gto) -> None:
    """Desfaz o sinal verde (o humano se enganou)."""
    with SessionLocal() as s:
        s.query(ConfirmacaoNome).filter(ConfirmacaoNome.gto == str(gto)).delete()
        s.commit()


def nome_confirmado(gto) -> bool:
    with SessionLocal() as s:
        return s.query(ConfirmacaoNome).filter(ConfirmacaoNome.gto == str(gto)).first() is not None


def confirmacoes_set() -> set:
    """Todos os gtos com sinal verde — a esteira carrega uma vez por execução."""
    try:
        with SessionLocal() as s:
            return {str(c.gto) for c in s.query(ConfirmacaoNome.gto).all()}
    except Exception:
        return set()


def _tentativas_ja_falhou(s, gto) -> int:
    """Quantas vezes a esteira JA olhou esta guia e NAO faturou (historico real, de
    qualquer execucao). Semeia o contador da fila pra o teto refletir a REALIDADE —
    nao reiniciar do zero uma guia que ja falhou N vezes (o furo que o dono achou
    13/08: 195831154 falhou a leitura 4x e ainda se dizia 'transitorio, do zero')."""
    return (s.query(ExecucaoItem)
            .filter(ExecucaoItem.gto == str(gto),
                    ExecucaoItem.faturado == False)  # noqa: E712
            .count())


def retry_na_fila(gto) -> bool:
    """A guia JA esta na fila (nao resolvida)? Serve pro aviso ao dono sair so na
    PRIMEIRA vez — sem isso, cada rodada de retry que falhasse de novo mandaria a
    mesma guia no WhatsApp outra vez."""
    with SessionLocal() as s:
        return (s.query(RetryFila)
                .filter(RetryFila.gto == str(gto), RetryFila.resolvido == False)  # noqa: E712
                .first()) is not None


def registrar_retry(gto, conta, dia, motivo, categoria: str = "", paciente: str = "") -> bool:
    """Enfileira um TRANSITORIO pra re-tentar. Externo/logica sao ignorados (nao
    retenta cego). Idempotente: se ja esta na fila (nao resolvido), so atualiza o
    ultimo_erro. O contador ja NASCE semeado com as falhas historicas (nao do zero),
    entao uma guia que ja falhou o teto de vezes entra ja ESGOTADA (nao vira retry
    cego). Retorna True se entrou (ou ja estava)."""
    if not deve_entrar_no_retry(motivo, categoria):
        return False
    _classe = "transitorio" if classe_retry(motivo, categoria) == "transitorio" else "nosso"
    from datetime import timedelta
    with SessionLocal() as s:
        it = (s.query(RetryFila)
              .filter(RetryFila.gto == str(gto), RetryFila.resolvido == False)  # noqa: E712
              .first())
        if it is None:
            _seed = _tentativas_ja_falhou(s, gto)
            _esgotou = not deve_retentar(_classe, _seed)
            s.add(RetryFila(gto=str(gto), conta=str(conta), dia=str(dia),
                            paciente=str(paciente or "")[:120],
                            classe=_classe, tentativas=_seed,
                            resolvido=_esgotou,   # ja nasceu esgotada -> nao retenta cego
                            ultimo_erro=str(motivo or "")[:300],
                            proximo_em=_now() + timedelta(minutes=retry_backoff_min(_seed))))
        else:
            it.ultimo_erro = str(motivo or "")[:300]
        s.commit()
    return True


def resolver_retry(gto) -> None:
    """Guia recuperou (faturou) -> sai da fila."""
    with SessionLocal() as s:
        for it in (s.query(RetryFila)
                   .filter(RetryFila.gto == str(gto), RetryFila.resolvido == False)):  # noqa: E712
            it.resolvido = True
        s.commit()


def bump_retry(gto) -> None:
    """Uma tentativa foi feita e falhou de novo -> incrementa e re-agenda (ou esgota
    o teto -> resolvido=True, vira pendencia 'nossa, nao recuperou')."""
    from datetime import timedelta
    with SessionLocal() as s:
        it = (s.query(RetryFila)
              .filter(RetryFila.gto == str(gto), RetryFila.resolvido == False)  # noqa: E712
              .first())
        if not it:
            return
        it.tentativas = (it.tentativas or 0) + 1
        _esgotou = None
        if not deve_retentar(it.classe or "transitorio", it.tentativas):
            it.resolvido = True
            # snapshot ANTES do commit: fora da sessao o objeto expira
            _esgotou = {"gto": it.gto, "paciente": it.paciente, "dia": it.dia,
                        "conta": it.conta, "motivo": it.ultimo_erro,
                        "tentativas": it.tentativas}
        else:
            it.proximo_em = _now() + timedelta(minutes=retry_backoff_min(it.tentativas))
        s.commit()
    # O try again acabou e nao recuperou. E a UNICA classe que precisa do dono — e
    # mesmo assim nao volta pro painel do operador (ele nao conserta bug nosso).
    if _esgotou:
        try:
            import notificador
            notificador.avisar_esgotou(**_esgotou)
        except Exception as e:
            print(f"[db] aviso esgotou falhou: {e}", flush=True)


def desfazer_bump(gto) -> None:
    """Devolve a tentativa que a guia gastou numa falha que NAO era dela (apagao de
    proxy/login). Reabre a linha se aquele bump foi justamente o que a fechou — sem
    isso, a guia sai do loop por causa de uma queda global e so volta na mao, que foi
    exatamente o estrago de 22/08."""
    from datetime import timedelta
    with SessionLocal() as s:
        it = (s.query(RetryFila)
              .filter(RetryFila.gto == str(gto))
              .order_by(RetryFila.id.desc()).first())
        if not it:
            return
        it.tentativas = max(0, (it.tentativas or 0) - 1)
        it.resolvido = False
        it.proximo_em = _now() + timedelta(minutes=retry_backoff_min(it.tentativas))
        s.commit()


PAUSA_PADRAO_MIN = 30


def pausar_retry(minutos: int = PAUSA_PADRAO_MIN, motivo: str = "") -> None:
    """Para a fila inteira por um tempo. Enquanto o mundo esta fora do ar, cada
    rodada de retry so gasta orcamento das guias e enche o WhatsApp do dono."""
    from datetime import timedelta
    with SessionLocal() as s:
        c = s.get(CronState, 1)
        if not c:
            c = CronState(id=1); s.add(c)
        c.retry_pausado_ate = _now() + timedelta(minutes=int(minutos))
        c.retry_pausa_motivo = str(motivo or "")[:500]
        s.commit()


def retry_pausado() -> bool:
    """A fila esta parada agora? Passado o prazo ela volta sozinha — se o mundo ainda
    estiver fora, a proxima rodada detecta de novo e pausa outra vez."""
    try:
        with SessionLocal() as s:
            c = s.get(CronState, 1)
            return bool(c and c.retry_pausado_ate and c.retry_pausado_ate > _now())
    except Exception:
        return False   # na duvida NAO trava o loop


def retry_pausa_info() -> dict:
    try:
        with SessionLocal() as s:
            c = s.get(CronState, 1)
            if not c or not c.retry_pausado_ate:
                return {}
            return {"ate": c.retry_pausado_ate, "motivo": c.retry_pausa_motivo,
                    "ativa": c.retry_pausado_ate > _now()}
    except Exception:
        return {}


def escalou_recentemente(gto, horas: int = 24) -> bool:
    """A guia ja ESGOTOU o retry nas ultimas `horas`? (e portanto o dono ja recebeu
    o "precisa de voce")."""
    from datetime import timedelta
    try:
        with SessionLocal() as s:
            n = (s.query(RetryFila)
                 .filter(RetryFila.gto == str(gto),
                         RetryFila.resolvido == True,               # noqa: E712
                         RetryFila.tentativas >= MAX_RETRIES_TRANSITORIO,
                         RetryFila.atualizado_em >= _now() - timedelta(hours=int(horas)))
                 .count())
        return n > 0
    except Exception:
        return False            # na duvida AVISA — perder aviso e pior que repetir


def deve_avisar_na_rodada(gto) -> bool:
    """Esta guia entra no resumo da rodada?

    NAO entra se acabou de ESGOTAR o retry. Medido nos alertas de 23/08: 6 dos 18
    avisos eram a mesma guia duas vezes em minutos (FABRICIO 06:11/06:14, as duas
    DILMA 06:35/06:37, HOSANA 07:09/07:11). E o pior nao era o barulho: o resumo diz
    "o sistema ja esta re-tentando", o que e FALSO para quem esgotou o teto — a
    segunda mensagem desmentia a primeira, que dizia "precisa de voce"."""
    return not escalou_recentemente(gto, horas=24)


def retries_devidos(limite: int = 50) -> list:
    """Itens prontos pra re-tentar agora (proximo_em <= agora, nao resolvido).
    O worker agrupa por (dia, conta) e re-roda a esteira com apenas_gtos."""
    with SessionLocal() as s:
        q = (s.query(RetryFila)
             .filter(RetryFila.resolvido == False,                      # noqa: E712
                     RetryFila.proximo_em <= _now())
             .order_by(RetryFila.proximo_em.asc()).limit(limite))
        return [{"gto": r.gto, "conta": r.conta, "dia": r.dia,
                 "tentativas": r.tentativas} for r in q.all()]


_TITULO_GRUPO = {
    "prenome_mal_lido": "Só o primeiro nome não bate — conferir",
    "modelo_sem_render": "Modelo sem o render 3D gerado",
    "esperando_analise": "Esperando o laudo da análise cefalométrica",
    "sem_entregavel": "Exame sem laudo e sem imagem",
    "esperando_tele": "Esperando o laudo da telerradiografia (traçado)",
    "falta_laudo": "Esperando o laudo do radiologista",
    "pedido_ilegivel": "Pedido do dentista com caligrafia ilegível",
    "pedido_nao_cobre": "O pedido do dentista não cobre a guia",
    "sem_pedido": "Não há pedido do dentista no prontuário",
    "data_vencida": "Pedido certo, mas com data vencida — anexar à mão",
    "nome_nao_bate": "O nome da solicitação não bate com o da guia",
    "solic_nao_confirmada": "Solicitação não confirmada — conferir",
    "revisao_humana": "Revisão humana",
    "data_ajustada": "Data ajustada — conferir",
    "guia_ilegivel": "Não conseguimos ler o que a guia autoriza",
    "anexacao": "A anexação foi barrada",
    "falha_tecnica": "Falha técnica do robô",
    "paciente_nao_achado": "Paciente não encontrado no PRORADIS",
    "multi_dia": "Exame encontrado em mais de um dia",
    "homonimo": "Mais de um paciente com o mesmo nome",
    "outros": "Outros",
}


def pendencias_do_dia(dia: str, contas: list = None) -> dict:
    """Só as pendências de um dia, agrupadas por quem precisa agir.

    Reaproveita relatorio_dia() — mesma consolidação, mesma regra de 'a informação
    mais recente vence e faturada em qualquer execução conta como faturada'. Aqui
    o recorte é outro: o que ainda depende de alguém, e de quem."""
    d = relatorio_dia(dia, contas)
    grupos = {}
    for i in d.get("pendentes_lista") or []:
        chave, quem, acao = classificar_pendencia(i.get("motivo"), i.get("categoria"))
        # A chave sozinha nao decide se e tecnica: 'outros' com categoria='erro' e
        # falha nossa, 'outros' sem categoria e conferencia. Por isso a chave do grupo
        # carrega o flag — senao um item tecnico pegava carona num grupo do operador.
        tec = eh_nosso(i.get("motivo"), i.get("categoria"))
        g = grupos.setdefault((chave, tec),
                              {"chave": chave, "titulo": _TITULO_GRUPO.get(chave, chave),
                               "responsavel": _NOSSO if tec else quem,
                               "acao": acao, "tecnica": tec, "itens": []})
        g["itens"].append(i)
    _ordem = {"Radiologista": 0, "Clínica": 1, "Cadastro": 2, "Conferência": 3}
    todos = sorted(grupos.values(),
                   key=lambda g: (_ordem.get(g["responsavel"], 9), -len(g["itens"])))
    for g in todos:
        g["total"] = len(g["itens"])
        g["itens"].sort(key=lambda x: (x.get("unidade") or "", x.get("paciente") or ""))
    # FALHA NOSSA NÃO É TAREFA DA OPERAÇÃO. Regra do dono (30/07, endurecida em
    # 22/08): "o que nós resolvemos aqui deve entrar num fallback até ser resolvido,
    # não deve ir para pendências". Uma guia que não faturou por bug nosso não tem o
    # que a recepção fazer — pedir pedido novo à clínica seria trabalho jogado fora,
    # porque o documento certo já está lá. Ela fica na FILA TÉCNICA, que a partir de
    # 22/08 é SÓ-ADMIN (não vai pra tela da RadioBras nem pro Excel da clínica): o
    # dono é avisado no WhatsApp e o loop de retry tenta de novo sozinho.
    lista = [g for g in todos if not g.get("tecnica")]
    fila = [g for g in todos if g.get("tecnica")]
    por_quem = {}
    for g in lista:
        por_quem[g["responsavel"]] = por_quem.get(g["responsavel"], 0) + g["total"]
    return {
        "dia": d.get("dia"), "contas": d.get("contas"),
        "grupos": lista,
        "total": sum(g["total"] for g in lista),          # só o que depende de gente
        "fila_tecnica": fila,
        "total_fila": sum(g["total"] for g in fila),
        "faturadas": d.get("resumo", {}).get("faturadas", 0),
        "total_guias": d.get("resumo", {}).get("total", 0),
        "por_responsavel": por_quem,
        "por_unidade": d.get("por_unidade") or [],
    }



def _dias_do_intervalo(dia_ini: str, dia_fim: str = None, teto: int = 62) -> list:
    """['24/07/2026', '25/07/2026', ...] entre as duas datas, inclusive.

    Teto de 62 dias porque cada dia e uma consulta ao banco — um intervalo aberto
    por engano (2020 ate hoje) travaria a tela sem dizer por que."""
    def _p(x):
        d, m, a = str(x or "").split("/")
        return date(int(a), int(m), int(d))
    try:
        ini = _p(dia_ini)
    except Exception:
        return []
    try:
        fim = _p(dia_fim) if dia_fim else ini
    except Exception:
        fim = ini
    if fim < ini:
        ini, fim = fim, ini
    n = min((fim - ini).days, teto - 1)
    return [(ini + timedelta(days=i)).strftime("%d/%m/%Y") for i in range(n + 1)]


def pendencias_do_periodo(dia_ini: str, dia_fim: str = None, contas: list = None) -> dict:
    """Pendencias de UM DIA ou de um INTERVALO, agrupadas por quem precisa agir.

    O dono pediu o intervalo (30/07) porque a pergunta real e "o que esta parado
    nesta semana?", nao "o que esta parado na terca". Uma pendencia sozinha num dia
    nao vira tarefa de ninguem; sete espalhadas pela semana viram.

    Tambem devolve `unidades_fora`: unidades que TEM execucao no periodo mas ficaram
    de fora do filtro. Sem isso a tela dizia "0 pendencias" mostrando so uma unidade,
    enquanto outra tinha 6 — o numero era verdadeiro e a leitura, falsa. Caso real:
    25/07, Centro 20/0 na tela, Tancredo com 6 pendencias invisiveis."""
    dias = _dias_do_intervalo(dia_ini, dia_fim)
    if not dias:
        return {"dia": "", "dias": [], "grupos": [], "total": 0, "fila_tecnica": [],
                "total_fila": 0, "faturadas": 0, "total_guias": 0,
                "por_responsavel": {}, "por_unidade": [], "contas": contas or [],
                "unidades_fora": [], "dias_sem_execucao": []}
    juntos, fila_j = {}, {}
    total_guias = faturadas = 0
    fora, sem_exec, uni = set(), [], {}
    for dia in dias:
        d = pendencias_do_dia(dia, contas)
        total_guias += d.get("total_guias") or 0
        faturadas += d.get("faturadas") or 0
        todas_do_dia = (relatorio_dia(dia, None).get("contas")) or []
        if not todas_do_dia:
            sem_exec.append(dia)
        if contas:
            fora |= {c for c in todas_do_dia if c not in contas}
        for u in d.get("por_unidade") or []:
            a = uni.setdefault(u["unidade"], {"unidade": u["unidade"], "total": 0,
                                              "faturadas": 0, "pendentes": 0})
            a["total"] += u.get("total") or 0
            a["faturadas"] += u.get("faturadas") or 0
            a["pendentes"] += u.get("pendentes") or 0
        for origem, destino in ((d.get("grupos") or [], juntos),
                                (d.get("fila_tecnica") or [], fila_j)):
            for g in origem:
                alvo = destino.setdefault(g["chave"], {**g, "itens": []})
                for i in g["itens"]:
                    destino[g["chave"]]["itens"].append({**i, "dia": dia})
    def _fecha(dic):
        _ordem = {"Radiologista": 0, "Clínica": 1, "Cadastro": 2, "Conferência": 3}
        lst = sorted(dic.values(),
                     key=lambda g: (_ordem.get(g["responsavel"], 9), -len(g["itens"])))
        for g in lst:
            g["total"] = len(g["itens"])
            g["itens"].sort(key=lambda x: (x.get("dia") or "", x.get("unidade") or "",
                                           x.get("paciente") or ""))
        return lst
    lista, fila = _fecha(juntos), _fecha(fila_j)
    por_quem = {}
    for g in lista:
        por_quem[g["responsavel"]] = por_quem.get(g["responsavel"], 0) + g["total"]
    return {
        "dia": dias[0], "dia_fim": dias[-1], "dias": dias,
        "periodo": len(dias) > 1,
        "contas": contas or [],
        "grupos": lista, "total": sum(g["total"] for g in lista),
        "fila_tecnica": fila, "total_fila": sum(g["total"] for g in fila),
        "faturadas": faturadas, "total_guias": total_guias,
        "por_responsavel": por_quem,
        "por_unidade": sorted(uni.values(), key=lambda x: x["unidade"]),
        "unidades_fora": sorted(fora),
        "dias_sem_execucao": sem_exec,
    }


def relatorio_dia(dia: str, contas: list = None) -> dict:
    """Fechamento CONSOLIDADO de um dia (todas as unidades ou as informadas).

    Um mesmo dia costuma ter VÁRIAS execuções (re-runs). Aqui a informação mais
    RECENTE de cada GTO vence — e uma GTO faturada em qualquer execução conta como
    faturada (não volta a aparecer como pendente por causa de um run antigo).
    Retorna {dia, contas, itens[], por_unidade[], resumo{}}.
    """
    from config import PLANOS
    with SessionLocal() as s:
        q = (s.query(Execucao).filter(Execucao.dia == dia)
             .order_by(Execucao.criado_em.asc()))          # antigo -> novo
        execs = [e for e in q.all() if not contas or (e.conta in contas)]
        por_gto = {}                                       # gto -> item consolidado
        for e in execs:
            for it in e.itens:
                k = str(it.gto)
                cur = por_gto.get(k)
                novo = {
                    "gto": k, "paciente": it.paciente, "conta": e.conta,
                    # NUNCA None: existem execuções antigas sem conta (9 no banco em
                    # 25/07) e o sorted() abaixo compara unidade — None x str estoura
                    # TypeError e derruba a tela, o PDF e o Excel do dia inteiro.
                    "unidade": ((PLANOS.get(e.conta, {}) or {}).get("label")
                                or e.conta or "(sem unidade)"),
                    "categoria": it.categoria, "faturado": bool(it.faturado),
                    "motivo": it.motivo or "", "solicitacao": it.solicitacao or "",
                    "exames_gto": it.exames_gto or "", "exames_lidos": it.exames_lidos or "",
                    "n_arquivos": it.n_arquivos or 0,
                    "quando": e.criado_em, "execucao_id": e.id, "dry_run": bool(e.dry_run),
                }
                # execução REAL sempre vence DRY; entre iguais, a mais recente;
                # e uma vez faturado, continua faturado.
                if cur is None:
                    por_gto[k] = novo
                else:
                    if cur["faturado"] and not novo["faturado"]:
                        continue                            # não "desfatura"
                    if cur["dry_run"] and not novo["dry_run"]:
                        por_gto[k] = novo
                    elif bool(cur["dry_run"]) == bool(novo["dry_run"]):
                        por_gto[k] = novo                   # mais recente (ordem asc)
        # RESSALVA: a regra "não desfatura" congela o status de uma GTO já anexada.
        # Se o backlog ainda tem pendência ABERTA para ela, o relatório dizia
        # "Faturada" enquanto a tela de Revisão dizia o contrário. Aqui a GTO
        # continua faturada (é a verdade do portal), mas leva a ressalva junto —
        # sem isso, os dois números do sistema se contradizem.
        _abertas = {}
        try:
            q = s.query(Pendencia).filter(Pendencia.dia == dia,
                                          Pendencia.resolvido == False)   # noqa: E712
            for p in q.all():
                if not contas or (p.conta in contas):
                    _abertas[str(p.gto)] = p.motivo or ""
        except Exception:
            _abertas = {}
        for i in por_gto.values():
            i["pendencia_aberta"] = i["faturado"] and i["gto"] in _abertas
            if i["pendencia_aberta"]:
                i["ressalva"] = _abertas.get(i["gto"], "")
        itens = sorted(por_gto.values(),
                       key=lambda x: (x["unidade"], not x["faturado"], x["paciente"] or ""))
        # agregação por unidade
        uni = {}
        for i in itens:
            u = uni.setdefault(i["unidade"], {"unidade": i["unidade"], "conta": i["conta"],
                                              "total": 0, "faturadas": 0, "pendentes": 0,
                                              "por_categoria": {}})
            u["total"] += 1
            if i["faturado"]:
                u["faturadas"] += 1
            else:
                u["pendentes"] += 1
                c = i["categoria"] or "outros"
                u["por_categoria"][c] = u["por_categoria"].get(c, 0) + 1
        cats = {}
        for i in itens:
            if not i["faturado"]:
                c = i["categoria"] or "outros"
                cats[c] = cats.get(c, 0) + 1
        return {
            "dia": dia,
            "contas": contas or sorted({e.conta for e in execs if e.conta}),
            "itens": itens,
            "faturadas_lista": [i for i in itens if i["faturado"]],
            "pendentes_lista": [i for i in itens if not i["faturado"]],
            "por_unidade": sorted(uni.values(), key=lambda x: x["unidade"]),
            "resumo": {
                "total": len(itens),
                "faturadas": sum(1 for i in itens if i["faturado"]),
                "pendentes": sum(1 for i in itens if not i["faturado"]),
                "por_categoria": cats,
                "execucoes": len(execs),
            },
        }


def listar_execucoes(limit: int = 100) -> list:
    """Histórico de execuções (consolidado) — mais recentes primeiro."""
    with SessionLocal() as s:
        rows = s.query(Execucao).order_by(Execucao.criado_em.desc()).limit(limit).all()
        return [{
            "id": e.id, "dia": e.dia, "conta": e.conta, "criado_em": e.criado_em,
            "dry_run": e.dry_run, "erro": e.erro,
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
        # EVIDÊNCIA: estes campos eram gravados desde o PR#20 e não saíam em lugar
        # nenhum — nem na tela, nem no JSON. Toda pergunta "por que essa não
        # faturou?" morria aqui: a resposta estava no banco, inalcançável. Sem
        # eles, casos como DOMINGOS (paciente não achado), CLISSIA (sem laudo) e
        # ELIENE (filtro tirou todos os laudos) só se explicavam por adivinhação.
        itens = [{
            "gto": it.gto, "paciente": it.paciente, "categoria": it.categoria,
            "faturado": it.faturado, "motivo": it.motivo, "solicitacao": it.solicitacao,
            "exames_gto": it.exames_gto, "exames_lidos": it.exames_lidos,
            "n_arquivos": it.n_arquivos,
            "paciente_lido": it.paciente_lido,      # o nome que a IA leu no papel
            "funil": it.funil,                      # prontuário -> candidatos -> descartados
            "arquivos_plano": it.arquivos_plano,    # o que ia/foi anexado
            "excluidos": it.excluidos,              # o que saiu do plano
            "data_exame_real": it.data_exame_real,  # exame veio de outro dia
        } for it in e.itens]
        return {
            "id": e.id, "dia": e.dia, "conta": e.conta, "criado_em": e.criado_em,
            "dry_run": e.dry_run, "erro": e.erro, "log": e.log,
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
    try:
        _seed_admin()
    except Exception as e:
        print(f"[db] seed admin: {e}", flush=True)


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
        "ALTER TABLE cron_state ADD COLUMN IF NOT EXISTS resumo_fat_last_at TIMESTAMPTZ",
        "ALTER TABLE retry_fila ADD COLUMN IF NOT EXISTS paciente VARCHAR(120)",
        "ALTER TABLE cron_state ADD COLUMN IF NOT EXISTS retry_pausado_ate TIMESTAMPTZ",
        "ALTER TABLE cron_state ADD COLUMN IF NOT EXISTS retry_pausa_motivo TEXT",
        "ALTER TABLE execucoes ADD COLUMN IF NOT EXISTS conta VARCHAR(20)",
        "ALTER TABLE execucoes ADD COLUMN IF NOT EXISTS log TEXT",
        "ALTER TABLE execucoes ADD COLUMN IF NOT EXISTS erro TEXT",
        "ALTER TABLE execucoes ADD COLUMN IF NOT EXISTS gemini_chamadas INTEGER DEFAULT 0",
        "ALTER TABLE execucoes ADD COLUMN IF NOT EXISTS gemini_tokens_in INTEGER DEFAULT 0",
        "ALTER TABLE execucoes ADD COLUMN IF NOT EXISTS gemini_tokens_out INTEGER DEFAULT 0",
        "ALTER TABLE execucao_itens ADD COLUMN IF NOT EXISTS arquivos_plano TEXT",
        "ALTER TABLE execucao_itens ADD COLUMN IF NOT EXISTS excluidos TEXT",
        "ALTER TABLE execucao_itens ADD COLUMN IF NOT EXISTS funil VARCHAR(120)",
        "ALTER TABLE execucao_itens ADD COLUMN IF NOT EXISTS paciente_lido VARCHAR(160)",
        # texto longo: limite de tamanho fazia a execucao INTEIRA falhar ao gravar
        "ALTER TABLE execucao_itens ALTER COLUMN paciente_lido TYPE TEXT",
        "ALTER TABLE execucao_itens ALTER COLUMN funil TYPE TEXT",
        "ALTER TABLE execucao_itens ALTER COLUMN solicitacao TYPE TEXT",
        "ALTER TABLE execucao_itens ALTER COLUMN paciente TYPE TEXT",
        "ALTER TABLE execucao_itens ADD COLUMN IF NOT EXISTS data_exame_real VARCHAR(10)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uix_pendencias ON pendencias (conta, dia, gto)",
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


def _melhores_execucoes_periodo(s, de_iso: str, ate_iso: str):
    """Melhor execução (pipeline novo) por dia no período: prefere real (não-dry)
    e, em empate, a de maior id (mais recente). Evita contar re-runs/simulações."""
    try:
        de = datetime.fromisoformat(de_iso).date()
        ate = datetime.fromisoformat(ate_iso).date()
    except Exception:
        return []
    por_dia = {}
    for e in s.query(Execucao).all():
        d = _dia_to_date(e.dia)
        if not d or d < de or d > ate:
            continue
        # Chave (dia, CONTA): sem a conta, as 3 unidades do mesmo dia colidiam e só
        # UMA sobrevivia — o relatório de período descartava as outras duas.
        k = (e.dia, e.conta or "")
        cur = por_dia.get(k)
        melhor = (cur is None
                  or (cur.dry_run and not e.dry_run)
                  or (bool(cur.dry_run) == bool(e.dry_run) and (e.id or 0) > (cur.id or 0)))
        if melhor:
            por_dia[k] = e
    return list(por_dia.values())


def gtos_por_plano_periodo(de_iso: str, ate_iso: str) -> dict:
    """Para cada plano, contagem empilhada por desfecho no período.
    Retorna {slug: {anexadas, sem_laudo, erros, simulacao, revisao, total, dias}}.
    Soma o pipeline antigo (runs) e o novo (execucoes -> slug 'odontoprev')."""
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
        # pipeline novo (Faturar dia) -> tudo no plano 'odontoprev' (RedeUna)
        _dias_vistos = set()
        for e in _melhores_execucoes_periodo(s, de_iso, ate_iso):
            a = out.setdefault("odontoprev", {"anexadas": 0, "sem_laudo": 0, "erros": 0,
                                              "simulacao": 0, "revisao": 0, "total": 0,
                                              "dias": 0})
            # conta DIAS distintos: agora há uma execução por unidade, e somar cada
            # uma triplicaria o rótulo "N dia(s)".
            if e.dia not in _dias_vistos:
                _dias_vistos.add(e.dia)
                a["dias"] += 1
            for it in e.itens:
                a["total"] += 1
                if e.dry_run:
                    a["simulacao"] += 1
                elif it.faturado:
                    a["anexadas"] += 1
                elif it.categoria == "revisao":
                    a["revisao"] += 1
                else:  # sem_solicitacao / sem justificativa
                    a["erros"] += 1
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


# ── Desfecho na RedeUna (pago/glosado/cancelado das guias que faturamos) ──────

DESFECHO_STATUS = [
    ("PAGA", "Paga"),
    ("GLOSADA", "Glosada"),
    ("CANCELADA", "Cancelada"),
    ("AGUARDANDO", "Aguardando repasse"),
]


def salvar_desfechos(lote: str, itens: list) -> int:
    """Grava o desfecho das guias de uma atualização (um 'lote'). Idempotente por
    (lote, conta): re-run do mesmo lote substitui as guias daquelas unidades."""
    def _v(x):
        return "" if x is None else (f"{x:.2f}" if isinstance(x, (int, float)) else str(x))
    with SessionLocal() as s:
        for conta in {i.get("conta") for i in itens}:
            s.query(GuiaDesfecho).filter(
                GuiaDesfecho.lote == lote, GuiaDesfecho.conta == conta).delete()
        for i in itens:
            s.add(GuiaDesfecho(
                lote=lote, conta=i.get("conta", ""), unidade=i.get("unidade", ""),
                gto=str(i.get("gto", "")), paciente=(i.get("paciente") or "")[:200],
                dia_faturado=i.get("dia_faturado", ""), status=i.get("status", ""),
                valor_bruto=_v(i.get("valor_bruto")), valor_glosado=_v(i.get("valor_glosado")),
                valor_pago=_v(i.get("valor_pago")), data_repasse=i.get("data_repasse", "") or "",
                glosa_cod=i.get("glosa_cod", ""), glosa_motivo=(i.get("glosa_motivo") or "")[:200],
                como_recursar=i.get("como_recursar") or "", recurso_estado=i.get("recurso_estado", ""),
                ortodontia=bool(i.get("ortodontia")), prazo_limite=i.get("prazo_limite", "") or "",
                prazo_dias=i.get("prazo_dias"), prescrito=bool(i.get("prescrito")),
            ))
        s.commit()
        return len(itens)


def _desfecho_lote_atual(s, lote: str = None) -> str:
    if lote:
        return lote
    r = s.query(GuiaDesfecho.lote).order_by(GuiaDesfecho.captured_at.desc()).first()
    return r[0] if r else None


def desfechos(lote: str = None, status: str = None, unidade: str = None) -> list:
    """Lista as guias de um lote (default = mais recente), opcionalmente filtrando
    por status/unidade. Ordena por prazo (as que vencem antes primeiro)."""
    with SessionLocal() as s:
        lote = _desfecho_lote_atual(s, lote)
        if not lote:
            return []
        q = s.query(GuiaDesfecho).filter(GuiaDesfecho.lote == lote)
        if status:
            q = q.filter(GuiaDesfecho.status == status)
        if unidade:
            q = q.filter(GuiaDesfecho.unidade == unidade)
        out = []
        for e in q.all():
            out.append({
                "conta": e.conta, "unidade": e.unidade, "gto": e.gto, "paciente": e.paciente,
                "dia_faturado": e.dia_faturado, "status": e.status,
                "valor_bruto": e.valor_bruto, "valor_glosado": e.valor_glosado,
                "valor_pago": e.valor_pago, "data_repasse": e.data_repasse,
                "glosa_cod": e.glosa_cod, "glosa_motivo": e.glosa_motivo,
                "como_recursar": e.como_recursar, "recurso_estado": e.recurso_estado,
                "ortodontia": e.ortodontia, "prazo_limite": e.prazo_limite,
                "prazo_dias": e.prazo_dias, "prescrito": e.prescrito,
            })
        # prazo mais curto primeiro (None = sem prazo vai pro fim)
        out.sort(key=lambda x: (x["prazo_dias"] is None, x["prazo_dias"] if x["prazo_dias"] is not None else 0))
        return out


def desfecho_panorama(lote: str = None) -> dict:
    """Resumo por status + por unidade + valores, do lote mais recente."""
    with SessionLocal() as s:
        lote = _desfecho_lote_atual(s, lote)
        if not lote:
            return {"lote": None, "total": 0, "por_status": {}, "por_unidade": [],
                    "status_labels": DESFECHO_STATUS, "total_pago": 0.0, "total_glosado": 0.0,
                    "a_recorrer": 0, "prescritas": 0}
        evs = s.query(GuiaDesfecho).filter(GuiaDesfecho.lote == lote).all()
        por_status = {k: 0 for k, _ in DESFECHO_STATUS}
        por_unidade = {}
        tot_pago = tot_glosado = 0.0
        a_recorrer = prescritas = 0

        def _f(x):
            try:
                return float(x) if x else 0.0
            except (TypeError, ValueError):
                return 0.0
        for e in evs:
            por_status[e.status] = por_status.get(e.status, 0) + 1
            u = por_unidade.setdefault(e.unidade, {"unidade": e.unidade, "total": 0,
                                                   **{k: 0 for k, _ in DESFECHO_STATUS}})
            u["total"] += 1
            u[e.status] = u.get(e.status, 0) + 1
            tot_pago += _f(e.valor_pago)
            tot_glosado += _f(e.valor_glosado)
            if e.status == "GLOSADA" and e.recurso_estado == "RECURSAVEL" and not e.prescrito:
                a_recorrer += 1
            if e.prescrito:
                prescritas += 1
        return {
            "lote": lote, "total": len(evs), "por_status": por_status,
            "por_unidade": sorted(por_unidade.values(), key=lambda x: -x["total"]),
            "status_labels": DESFECHO_STATUS,
            "total_pago": round(tot_pago, 2), "total_glosado": round(tot_glosado, 2),
            "a_recorrer": a_recorrer, "prescritas": prescritas,
        }


def guias_faturadas_por_nos(desde_dia: str = None) -> list:
    """Âncora da aba: as guias que NÓS faturamos (ExecucaoItem.faturado=True), a
    execução REAL mais recente por (gto). desde_dia = filtra por dia_faturado >=.
    Retorna [{gto, paciente, conta, unidade, dia_faturado}] deduplicado por gto."""
    UNIDADE = {"388336": "Centro, Lauro, Periperi e Itaigara",
               "397950": "Tancredo", "410923": "Camacari"}
    with SessionLocal() as s:
        q = (s.query(ExecucaoItem, Execucao)
             .join(Execucao, ExecucaoItem.execucao_id == Execucao.id)
             .filter(ExecucaoItem.faturado == True, Execucao.dry_run == False)  # noqa: E712
             .order_by(Execucao.criado_em.desc()))
        vistos, out = set(), []
        for it, ex in q.all():
            g = str(it.gto)
            if g in vistos:
                continue
            vistos.add(g)
            out.append({"gto": g, "paciente": it.paciente, "conta": ex.conta,
                        "unidade": UNIDADE.get(ex.conta, ex.conta), "dia_faturado": ex.dia})
        if desde_dia:
            def _key(d):
                try:
                    dd, mm, yy = d.split("/"); return (int(yy), int(mm), int(dd))
                except Exception:
                    return (0, 0, 0)
            alvo = _key(desde_dia)
            out = [o for o in out if _key(o["dia_faturado"]) >= alvo]
        return out


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
