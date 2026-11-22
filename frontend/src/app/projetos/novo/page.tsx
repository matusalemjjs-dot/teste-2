"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Page, RequireAuth } from "@/components/app-shell";
import { Alert, Button, Card, Field } from "@/components/ui";
import { UploadPanel } from "@/components/upload-panel";
import { api, ApiError, type Project } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function NewProject() {
  const { token } = useAuth();
  const router = useRouter();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      setProject(await api<Project>(token, "/api/v1/projects", {
        method: "POST", body: JSON.stringify({ title: f.get("titulo"), rights_confirmed: f.get("direitos") === "on" }),
      }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao criar o projeto.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page title="Novo projeto">
      <Card className="max-w-2xl">
        {!project ? (
          <form onSubmit={onSubmit} className="space-y-4">
            {error && <Alert>{error}</Alert>}
            <Field id="titulo" name="titulo" label="Título do projeto" maxLength={200} required />
            <div className="flex items-start gap-3">
              <input id="direitos" name="direitos" type="checkbox" required className="mt-1 h-5 w-5 accent-indigo-700" />
              <label htmlFor="direitos" className="text-sm text-slate-800">Confirmo que tenho autorização para
                processar este vídeo (sou o autor ou tenho permissão do titular dos direitos e das pessoas que aparecem nele).</label>
            </div>
            <Button type="submit" disabled={busy}>{busy ? "Criando…" : "Continuar"}</Button>
          </form>
        ) : (
          <>
            <h2 className="mb-4 font-semibold">{project.title}</h2>
            <UploadPanel projectId={project.id} onDone={() => router.push(`/projetos/${project.id}`)} />
          </>
        )}
      </Card>
    </Page>
  );
}

export default function NovoProjetoPage() {
  return <RequireAuth><NewProject /></RequireAuth>;
}
