"""Administração do beta por linha de comando (executar no servidor, com acesso SSH restrito por chave).

Não há rota web de administração: quem não tem acesso ao servidor não alcança estes comandos.
Todo comando que altera algo fica registrado na auditoria (`admin.*`, com o usuário do sistema operacional).

    python -m app.admin convites
    python -m app.admin convidar EMAIL [--enviar-email]
    python -m app.admin revogar EMAIL
    python -m app.admin usuarios
    python -m app.admin suspender EMAIL | reativar EMAIL
    python -m app.admin cota EMAIL [--minutos N] [--projetos-dia N]
    python -m app.admin periodo (EMAIL | --todos) --inicio AAAA-MM-DD --fim AAAA-MM-DD
    python -m app.admin relatorio [--horas 24] [--json]
"""

import argparse
import getpass
import json
import statistics
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.models import AuditEvent, ProcessingJob, Profile, Project, Subscription, UsageLedger
from app.services import audit, invites
from app.services.quotas import _llm_reserved, _llm_spent, _used_seconds, period_bounds


def operador() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "desconhecido"


def _registrar(db, acao: str, detalhes: dict) -> None:
    audit.record(db, None, f"admin.{acao}", "admin", None, {**detalhes, "operador": operador()})
    db.commit()


def _perfil(db, email: str) -> Profile:
    p = db.scalar(select(Profile).where(func.lower(Profile.email) == email.strip().lower()))
    if p is None:
        raise SystemExit(f"Usuário não encontrado: {email} (ele precisa ter entrado ao menos uma vez).")
    return p


def _assinatura(db, profile: Profile) -> Subscription:
    return db.scalar(select(Subscription).where(Subscription.owner_id == profile.id).with_for_update())


def enviar_convite_supabase(settings: Settings, email: str) -> str:
    """Pede ao Supabase o e-mail de convite (exige SUPABASE_SERVICE_ROLE_KEY). Não validado com Supabase real."""
    base = settings.supabase_admin_url or settings.supabase_url
    if not base or not settings.supabase_service_role_key:
        return "e-mail de convite NÃO enviado (sem SUPABASE_SERVICE_ROLE_KEY): convide pelo painel do Supabase"
    key = settings.supabase_service_role_key.get_secret_value()
    redirect = urllib.request.quote(f"{settings.frontend_url.rstrip('/')}/redefinir-senha", safe="")
    req = urllib.request.Request(f"{base.rstrip('/')}/auth/v1/invite?redirect_to={redirect}", method="POST",
                                 data=json.dumps({"email": email}).encode(),
                                 headers={"apikey": key, "Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 (URL da configuração)
            return f"e-mail de convite enviado pelo Supabase (HTTP {r.status})"
    except urllib.error.HTTPError as exc:
        return f"falha ao enviar o convite pelo Supabase (HTTP {exc.code}); convide pelo painel do Supabase"
    except (urllib.error.URLError, TimeoutError):
        return "Supabase inacessível; convide pelo painel do Supabase"


def _avaliacoes(db, desde: datetime) -> dict:
    """Última avaliação de cada corte na janela: útil x não útil e a nota estimada média de cada grupo."""
    ultima: dict = {}
    for res, det in db.execute(select(AuditEvent.resource_id, AuditEvent.details).where(
            AuditEvent.action == "clip.feedback", AuditEvent.created_at >= desde)
            .order_by(AuditEvent.created_at, AuditEvent.id)):
        ultima[res] = det or {}
    util = [d.get("nota") for d in ultima.values() if d.get("util")]
    nao = [d.get("nota") for d in ultima.values() if not d.get("util")]
    media = lambda xs: round(statistics.mean(x for x in xs if x is not None), 1) if any(  # noqa: E731
        x is not None for x in xs) else None
    return {"util": len(util), "nao_util": len(nao), "nota_media_util": media(util), "nota_media_nao_util": media(nao)}


def relatorio(db, settings: Settings, horas: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    desde = now - timedelta(hours=horas)
    jobs = db.scalars(select(ProcessingJob).where(ProcessingJob.finished_at >= desde)).all()
    feitos = [j for j in jobs if j.status == "succeeded" and j.job_type == "process_project"]
    falhas: dict[str, int] = {}
    for j in jobs:
        if j.status == "failed":
            falhas[j.error_code or "?"] = falhas.get(j.error_code or "?", 0) + 1
    duracoes, razoes = [], []
    for j in feitos:
        if j.started_at and j.finished_at:
            d = (j.finished_at - j.started_at).total_seconds()
            duracoes.append(d)
            secs = db.scalar(select(Project.source_duration_seconds).where(Project.id == j.project_id))
            if secs:
                razoes.append(d / float(secs))
    fila: dict = {"no_banco_na_fila": db.scalar(select(func.count()).select_from(ProcessingJob).where(
        ProcessingJob.status == "queued")), "rodando": db.scalar(select(func.count()).select_from(ProcessingJob)
                                                                 .where(ProcessingJob.status == "running"))}
    mais_antigo = db.scalar(select(func.min(ProcessingJob.created_at)).where(ProcessingJob.status == "queued"))
    fila["espera_mais_antiga_min"] = round((now - mais_antigo).total_seconds() / 60, 1) if mais_antigo else 0
    try:
        from rq import Worker

        from app.services.queue import get_queue, get_redis
        fila["no_redis"] = get_queue().count
        fila["workers_vivos"] = len(Worker.all(connection=get_redis()))
    except Exception:
        fila["no_redis"] = fila["workers_vivos"] = "redis_indisponivel"
    g_start, g_end = ((settings.beta_period_start, settings.beta_period_end)
                      if settings.beta_mode and settings.beta_period_start and settings.beta_period_end
                      else (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now + timedelta(days=1)))
    g_start = g_start.replace(tzinfo=UTC) if g_start.tzinfo is None else g_start
    g_end = g_end.replace(tzinfo=UTC) if g_end.tzinfo is None else g_end
    minutos = float(db.scalar(select(func.coalesce(func.sum(UsageLedger.quantity), 0)).where(
        UsageLedger.kind == "processed_seconds", UsageLedger.created_at >= desde)) or 0) / 60
    return {
        "janela_horas": horas, "gerado_em": now.isoformat(timespec="seconds"),
        "usuarios": {"total": db.scalar(select(func.count()).select_from(Profile)),
                     "com_projeto_na_janela": db.scalar(select(func.count(func.distinct(AuditEvent.actor_id))).where(
                         AuditEvent.action == "project.create", AuditEvent.created_at >= desde)),
                     "convites_ativos": len(invites.configured(settings) | invites.granted(db))},
        "projetos": {"criados": db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "project.create", AuditEvent.created_at >= desde)),
            "concluidos": len(feitos), "falhas_por_codigo": falhas},
        "minutos_processados": round(minutos, 1),
        "custo_ia_usd": {"na_janela": round(_llm_spent(db, None, desde, now + timedelta(seconds=1)), 4),
                         "no_periodo": round(_llm_spent(db, None, g_start, g_end), 4),
                         "reservado_agora": round(_llm_reserved(db, settings, None), 4),
                         "teto_total": settings.llm_budget_total_usd or None},
        "tempo_processamento": {
            "mediana_min": round(statistics.median(duracoes) / 60, 1) if duracoes else None,
            "p95_min": round(sorted(duracoes)[int(0.95 * (len(duracoes) - 1))] / 60, 1) if duracoes else None,
            "por_minuto_de_video": round(statistics.median(razoes), 2) if razoes else None},
        "fila": fila,
        "avaliacoes_dos_cortes": _avaliacoes(db, desde),
        "retencao_eventos_na_janela": db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action.like("retention.%"), AuditEvent.created_at >= desde)),
    }


def _imprimir_relatorio(r: dict) -> None:
    print(f"Relatório AutoClip IA — últimas {r['janela_horas']} h (gerado em {r['gerado_em']})")
    u, p, c, t, f = r["usuarios"], r["projetos"], r["custo_ia_usd"], r["tempo_processamento"], r["fila"]
    print(f"Usuários: {u['total']} (com projeto na janela: {u['com_projeto_na_janela']}; convites ativos: "
          f"{u['convites_ativos']})")
    falhas = p["falhas_por_codigo"] or "nenhuma"
    print(f"Projetos: {p['criados']} criados, {p['concluidos']} concluídos; falhas: {falhas}")
    print(f"Minutos processados: {r['minutos_processados']}")
    print(f"Custo de IA (US$): janela {c['na_janela']} | período {c['no_periodo']} | reservado {c['reservado_agora']}"
          f" | teto {c['teto_total']}")
    print(f"Tempo por vídeo: mediana {t['mediana_min']} min, p95 {t['p95_min']} min, "
          f"{t['por_minuto_de_video']} min por minuto de vídeo")
    print(f"Fila: {f['no_banco_na_fila']} na fila (Redis: {f['no_redis']}), {f['rodando']} rodando, "
          f"workers vivos: {f['workers_vivos']}, espera mais antiga {f['espera_mais_antiga_min']} min")
    a = r["avaliacoes_dos_cortes"]
    print(f"Avaliações dos cortes: {a['util']} úteis (nota média {a['nota_media_util']}), {a['nao_util']} não úteis "
          f"(nota média {a['nota_media_nao_util']})")
    print(f"Eventos de retenção na janela: {r['retencao_eventos_na_janela']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.admin", description="Administração do beta do AutoClip IA")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("convites", help="lista os convites efetivos")
    c = sub.add_parser("convidar", help="concede convite")
    c.add_argument("email")
    c.add_argument("--enviar-email", action="store_true", help="pede ao Supabase o e-mail de convite")
    sub.add_parser("revogar", help="revoga convite (vale em até 30 s)").add_argument("email")
    sub.add_parser("usuarios", help="lista usuários, consumo e custo no período")
    sub.add_parser("suspender").add_argument("email")
    sub.add_parser("reativar").add_argument("email")
    q = sub.add_parser("cota", help="ajusta a cota de um usuário")
    q.add_argument("email")
    q.add_argument("--minutos", type=int)
    q.add_argument("--projetos-dia", type=int)
    pe = sub.add_parser("periodo", help="define o período da cota")
    pe.add_argument("email", nargs="?")
    pe.add_argument("--todos", action="store_true")
    pe.add_argument("--inicio", required=True)
    pe.add_argument("--fim", required=True)
    r = sub.add_parser("relatorio", help="uso, custo, falhas, fila e tempos")
    r.add_argument("--horas", type=int, default=24)
    r.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    settings = get_settings()

    with get_sessionmaker()() as db:
        if args.cmd == "convites":
            rev = invites.revoked(db)
            todos = invites.configured(settings) | invites.granted(db) | rev
            print(f"Modo beta: {'ATIVO' if settings.beta_mode else 'desligado (todos entram)'}")
            for e in sorted(todos):
                origem = "configuração" if e in invites.configured(settings) else "comando"
                print(f"  {e}  [{origem}]{'  (REVOGADO)' if e in rev else ''}")
        elif args.cmd == "convidar":
            invites.grant(db, args.email, operador())
            print(f"Convite concedido: {args.email.lower()}")
            if args.enviar_email:
                print(enviar_convite_supabase(settings, args.email))
        elif args.cmd == "revogar":
            invites.revoke(db, args.email, operador())
            print(f"Convite revogado: {args.email.lower()} (a API passa a recusar em até 30 s)")
        elif args.cmd == "usuarios":
            now = datetime.now(UTC)
            print(f"{'e-mail':<34} {'status':<10} {'cota':>5} {'usado':>7} {'custo IA':>9} {'projetos':>8}")
            for prof in db.scalars(select(Profile).order_by(Profile.created_at)):
                s = db.scalar(select(Subscription).where(Subscription.owner_id == prof.id))
                if s is None:
                    continue
                ini, fim = period_bounds(s, settings, now)
                usado = _used_seconds(db, prof.id, ini, fim) / 60
                custo = _llm_spent(db, prof.id, ini, fim)
                n = db.scalar(select(func.count()).select_from(Project).where(Project.owner_id == prof.id))
                print(f"{(prof.email or '-'):<34} {s.status:<10} {s.monthly_minutes_quota:>5} {usado:>7.1f} "
                      f"{custo:>9.4f} {n:>8}")
        elif args.cmd in ("suspender", "reativar"):
            prof = _perfil(db, args.email)
            s = _assinatura(db, prof)
            s.status = "suspended" if args.cmd == "suspender" else "active"
            _registrar(db, args.cmd, {"usuario": str(prof.id)})
            print(f"{args.email}: {s.status}")
        elif args.cmd == "cota":
            prof = _perfil(db, args.email)
            s = _assinatura(db, prof)
            if args.minutos is not None:
                s.monthly_minutes_quota = args.minutos
            if args.projetos_dia is not None:
                s.max_projects_per_day = args.projetos_dia
            _registrar(db, "cota", {"usuario": str(prof.id), "minutos": s.monthly_minutes_quota,
                                    "projetos_dia": s.max_projects_per_day})
            print(f"{args.email}: {s.monthly_minutes_quota} min, {s.max_projects_per_day} projetos/dia")
        elif args.cmd == "periodo":
            ini = datetime.fromisoformat(args.inicio).replace(tzinfo=UTC)
            fim = datetime.fromisoformat(args.fim).replace(tzinfo=UTC) + timedelta(days=1)  # fim inclusivo
            if args.todos:
                alvos = db.scalars(select(Subscription).with_for_update()).all()
            elif args.email:
                alvos = [_assinatura(db, _perfil(db, args.email))]
            else:
                raise SystemExit("Informe um e-mail ou --todos.")
            for s in alvos:
                s.current_period_start, s.current_period_end = ini, fim
            _registrar(db, "periodo", {"inicio": args.inicio, "fim": args.fim, "assinaturas": len(alvos)})
            print(f"Período {args.inicio} a {args.fim} aplicado a {len(alvos)} assinatura(s).")
        elif args.cmd == "relatorio":
            rep = relatorio(db, settings, args.horas)
            print(json.dumps(rep, ensure_ascii=False, indent=2)) if args.json else _imprimir_relatorio(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
