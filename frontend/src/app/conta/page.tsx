"use client";

import { useState } from "react";
import { Page, RequireAuth } from "@/components/app-shell";
import { Alert, Button, Card, Field } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const CONFIRMACAO = "EXCLUIR";

function Conta() {
  const { token, email, signOut } = useAuth();
  const [texto, setTexto] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function excluir() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ message: string }>(token, "/api/v1/me", { method: "DELETE" });
      await signOut(`/?conta_excluida=${encodeURIComponent(r.message)}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Erro inesperado.");
      setBusy(false);
    }
  }

  return (
    <Page title="Minha conta">
      <Card className="mb-6 max-w-2xl">
        <p className="text-slate-800">Conectado como <strong>{email ?? "—"}</strong>.</p>
        <p className="mt-2 text-sm"><a href="/privacidade" className="text-indigo-800 underline">Como tratamos seus dados</a></p>
      </Card>
      <Card className="max-w-2xl border-red-200">
        <h2 className="mb-2 text-lg font-semibold text-red-900">Excluir conta</h2>
        <p className="mb-4 text-sm text-slate-800">Apaga permanentemente sua conta, todos os projetos, vídeos,
          transcrições e cortes. Não é possível desfazer.</p>
        {error && <div className="mb-4"><Alert>{error}</Alert></div>}
        <Field id="confirmacao" label={`Para confirmar, digite ${CONFIRMACAO}`} value={texto}
          onChange={(e) => setTexto(e.target.value)} autoComplete="off" />
        <Button variant="danger" className="mt-4" disabled={texto !== CONFIRMACAO || busy} onClick={excluir}>
          {busy ? "Excluindo…" : "Excluir minha conta"}</Button>
      </Card>
    </Page>
  );
}

export default function ContaPage() {
  return <RequireAuth><Conta /></RequireAuth>;
}
