"use client";

import Link from "next/link";
import { useState } from "react";
import { AuthCard } from "@/components/auth-form";
import { Alert, Button, Field } from "@/components/ui";
import { useAuth } from "@/lib/auth";

export default function RedefinirSenha() {
  const { updatePassword, token, ready } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const senha = String(f.get("senha"));
    if (senha !== String(f.get("confirmacao"))) return setError("As senhas não conferem.");
    if (senha.length < 8) return setError("Use uma senha com pelo menos 8 caracteres.");
    setBusy(true);
    const err = await updatePassword(senha);
    setBusy(false);
    if (err) setError(err);
    else setDone(true);
  }

  return (
    <AuthCard title="Criar senha">
      {done ? <Alert kind="success">Senha alterada. <Link className="underline" href="/painel">Ir para o painel</Link></Alert>
        : ready && !token ? <Alert>Link inválido ou expirado. <Link className="underline" href="/recuperar-senha">Peça um novo link</Link>.</Alert>
        : (
          <form onSubmit={onSubmit} className="space-y-4">
            {error && <Alert>{error}</Alert>}
            <Field id="senha" name="senha" type="password" label="Nova senha" autoComplete="new-password" minLength={8} required />
            <Field id="confirmacao" name="confirmacao" type="password" label="Confirme a nova senha" autoComplete="new-password" required />
            <Button type="submit" disabled={busy || !ready} className="w-full">{busy ? "Salvando…" : "Salvar nova senha"}</Button>
          </form>
        )}
    </AuthCard>
  );
}
