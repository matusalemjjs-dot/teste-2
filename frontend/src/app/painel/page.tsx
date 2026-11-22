"use client";

import Link from "next/link";
import { useCallback } from "react";
import { Page, RequireAuth } from "@/components/app-shell";
import { Alert, Card, StatusBadge } from "@/components/ui";
import { api, type Me, type Project } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, duration, minutes } from "@/lib/format";
import { useApiData } from "@/lib/use-api";

function Dashboard() {
  const { token } = useAuth();
  const fetcher = useCallback(() => Promise.all([
    api<Me>(token, "/api/v1/me"),
    api<{ items: Project[] }>(token, "/api/v1/projects?limit=100"),
    api<{ total: number }>(token, "/api/v1/projects?status=completed&limit=1"),
  ]), [token]);
  // atualiza a cada 5 s (status e progresso dos projetos em andamento)
  const { data, error: apiError } = useApiData(fetcher, 5000);
  const me = data?.[0] ?? null;
  const projects = data?.[1].items ?? null;
  const error = apiError?.message ?? null;

  const usage = me?.usage;
  const completed = data?.[2].total ?? 0; // total real (não só os 100 mais recentes da lista)
  return (
    <Page title="Painel">
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <div className="mb-8 grid gap-4 sm:grid-cols-3">
        <Card><p className="text-sm text-slate-600">Vídeos processados</p>
          <p className="mt-1 text-3xl font-bold" data-testid="stat-processados">{projects ? completed : "—"}</p></Card>
        <Card><p className="text-sm text-slate-600">Minutos consumidos no mês</p>
          <p className="mt-1 text-3xl font-bold" data-testid="stat-minutos">
            {usage ? `${minutes(usage.used_minutes)} / ${usage.quota_minutes}` : "—"}</p>
          {usage && usage.reserved_minutes > 0 && <p className="text-sm text-slate-600">
            + {minutes(usage.reserved_minutes)} min em processamento</p>}</Card>
        <Card><p className="text-sm text-slate-600">Projetos hoje</p>
          <p className="mt-1 text-3xl font-bold">{usage ? `${usage.projects_last_24h} / ${usage.projects_per_day}` : "—"}</p></Card>
      </div>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xl font-semibold">Seus projetos</h2>
        <Link href="/projetos/novo" className="rounded-lg bg-indigo-700 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-800">
          Novo projeto</Link>
      </div>
      {projects === null ? <p role="status" className="text-slate-600">Carregando…</p>
        : projects.length === 0 ? <Card><p className="text-slate-700">Você ainda não tem projetos.
          <Link href="/projetos/novo" className="ml-1 font-semibold text-indigo-800 underline">Crie o primeiro</Link>.</p></Card>
        : (
          <ul className="space-y-3" aria-label="Projetos">
            {projects.map((p) => (
              <li key={p.id}>
                <Link href={`/projetos/${p.id}`} className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm
                  hover:border-indigo-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold">{p.title}</span><StatusBadge status={p.status} />
                  </div>
                  <p className="mt-1 text-sm text-slate-600">
                    {dateTime(p.created_at)} · {duration(p.source_duration_seconds)}
                    {["queued", "processing"].includes(p.status) && ` · ${p.progress}%`}
                    {p.status === "failed" && p.status_message && ` · ${p.status_message}`}</p>
                </Link>
              </li>
            ))}
          </ul>
        )}
    </Page>
  );
}

export default function PainelPage() {
  return <RequireAuth><Dashboard /></RequireAuth>;
}
