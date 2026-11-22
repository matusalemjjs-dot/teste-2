"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthCard } from "@/components/auth-form";
import { Alert, Button, Field } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { BETA_MODE } from "@/lib/config";

function destino(): string {
  const v = new URLSearchParams(window.location.search).get("voltar");
  return v && v.startsWith("/") && !v.startsWith("//") ? v : "/painel"; // só caminhos internos
}

export default function Entrar() {
  const { signIn } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    const err = await signIn(String(f.get("email")), String(f.get("senha")));
    setBusy(false);
    if (err) setError(err);
    else router.push(destino());
  }

  return (
    <AuthCard title="Entrar" footer={BETA_MODE ? <>Beta fechado: o acesso é feito por convite.</>
      : <>Não tem conta? <Link className="font-semibold text-indigo-800 underline" href="/cadastro">Criar conta</Link></>}>
      <form onSubmit={onSubmit} className="space-y-4" noValidate={false}>
        {error && <Alert>{error}</Alert>}
        <Field id="email" name="email" type="email" label="E-mail" autoComplete="email" required />
        <Field id="senha" name="senha" type="password" label="Senha" autoComplete="current-password" required />
        <Button type="submit" disabled={busy} className="w-full">{busy ? "Entrando…" : "Entrar"}</Button>
        <p className="text-center text-sm"><Link className="text-indigo-800 underline" href="/recuperar-senha">Esqueci minha senha</Link></p>
      </form>
    </AuthCard>
  );
}
