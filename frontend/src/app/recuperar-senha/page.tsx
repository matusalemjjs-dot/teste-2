"use client";

import Link from "next/link";
import { useState } from "react";
import { AuthCard } from "@/components/auth-form";
import { Alert, Button, Field } from "@/components/ui";
import { useAuth } from "@/lib/auth";

export default function RecuperarSenha() {
  const { sendReset } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    const err = await sendReset(String(new FormData(e.currentTarget).get("email")));
    setBusy(false);
    if (err) setError(err);
    else setSent(true);
  }

  return (
    <AuthCard title="Recuperar senha" footer={<Link className="text-indigo-800 underline" href="/entrar">Voltar para entrar</Link>}>
      {sent ? <Alert kind="success">Se houver uma conta com este e-mail, você receberá um link para criar uma nova senha.</Alert> : (
        <form onSubmit={onSubmit} className="space-y-4">
          {error && <Alert>{error}</Alert>}
          <Field id="email" name="email" type="email" label="E-mail" autoComplete="email" required />
          <Button type="submit" disabled={busy} className="w-full">{busy ? "Enviando…" : "Enviar link"}</Button>
        </form>
      )}
    </AuthCard>
  );
}
