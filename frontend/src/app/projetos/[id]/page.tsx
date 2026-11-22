"use client";

import { useParams, useRouter } from "next/navigation";
import { Suspense, useCallback, useState } from "react";
import { Page, RequireAuth } from "@/components/app-shell";
import { Gallery } from "@/components/gallery";
import { Alert, Button, Card, ProgressBar, StatusBadge } from "@/components/ui";
import { UploadPanel } from "@/components/upload-panel";
import { api, ApiError, type Project, type ProjectStatusOut } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, duration } from "@/lib/format";
import { useApiData } from "@/lib/use-api";

function ProjectView({ id }: { id: string }) {
  const { token } = useAuth();
  const router = useRouter();
  const [actionError, setActionError] = useState<string | null>(null);
  const [polling, setPolling] = useState(true);
  const fetcher = useCallback(() => Promise.all([
    api<Project>(token, `/api/v1/projects/${id}`),
    api<ProjectStatusOut>(token, `/api/v1/projects/${id}/status`),
  ]), [token, id]);
  const { data, error: apiError, reload } = useApiData(fetcher, polling ? 3000 : null);
  const project = data?.[0] ?? null;
  const status = data?.[1] ?? null;
  const notFound = apiError?.status === 404;
  const error = apiError?.message ?? null;
  const active = !status || ["queued", "processing"].includes(status.status);
  if (polling !== active) setPolling(active); // consulta periódica só enquanto processa
  const load = reload;

  async function act(path: string, method = "POST", after?: () => void) {
    setActionError(null);
    try {
      await api(token, path, { method });
      if (after) after(); else load();
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : "Erro inesperado.");
      load();
    }
  }

  if (notFound) return <Page title="Projeto não encontrado"><Alert>Este projeto não existe ou não pertence à sua conta.</Alert></Page>;
  if (!project || !status) return <Page title="Projeto">{error ? <Alert>{error}</Alert> : <p role="status">Carregando…</p>}</Page>;

  const s = status.status;
  return (
    <Page title={project.title}>
      <Card className="mb-6">
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge status={s} />
          <span className="text-sm text-slate-600">Criado em {dateTime(project.created_at)} · vídeo de {duration(project.source_duration_seconds)}</span>
        </div>
        <div aria-live="polite" className="mt-4 space-y-3">
          {(s === "queued" || s === "processing") && <ProgressBar value={status.progress} label={status.status_message ?? "Processando"} />}
          {s === "failed" && <Alert>{status.status_message ?? "O processamento falhou."}</Alert>}
          {s === "pending" && status.error_code && <Alert>{status.status_message}</Alert>}
          {s === "completed" && <Alert kind="success">{status.status_message ?? "Processamento concluído."}</Alert>}
          {s === "canceled" && <Alert kind="info">Processamento cancelado.</Alert>}
          {actionError && <Alert>{actionError}</Alert>}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {(s === "uploaded" || s === "failed") && <Button onClick={() => act(`/api/v1/projects/${id}/process`)}>
            {s === "failed" ? "Tentar novamente" : "Iniciar processamento"}</Button>}
          {["pending", "uploaded", "queued", "processing"].includes(s) &&
            <Button variant="secondary" onClick={() => act(`/api/v1/projects/${id}/cancel`)}>Cancelar</Button>}
          {!["queued", "processing"].includes(s) && <Button variant="danger" onClick={() => {
            if (window.confirm("Excluir o projeto e todos os arquivos? Esta ação não pode ser desfeita."))
              act(`/api/v1/projects/${id}`, "DELETE", () => router.push("/painel"));
          }}>Excluir projeto</Button>}
        </div>
      </Card>
      {s === "pending" && <Card className="mb-6"><h2 className="mb-4 text-lg font-semibold">Enviar vídeo</h2>
        <UploadPanel projectId={id} onDone={load} onRefresh={load} /></Card>}
      {s === "completed" && <section aria-labelledby="cortes"><h2 id="cortes" className="mb-4 text-xl font-semibold">Cortes</h2>
        <Gallery projectId={id} /></section>}
    </Page>
  );
}

function ProjetoComParametro() {
  const params = useParams<{ id: string }>();
  return <RequireAuth><ProjectView id={params.id} /></RequireAuth>;
}

export default function ProjetoPage() {
  return (
    <Suspense fallback={<p className="p-8 text-center text-slate-600" role="status">Carregando…</p>}>
      <ProjetoComParametro />
    </Suspense>
  );
}
