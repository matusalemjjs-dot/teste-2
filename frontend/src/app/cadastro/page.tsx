"use client";

import Link from "next/link";
import { useState } from "react";
import { AuthCard } from "@/components/auth-form";
import { Alert, Button, Field } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { BETA_MODE } from "@/lib/config";

function ConvitePendente() {
  return (
    <AuthCard title="Acesso por convite" footer={<>Já recebeu o convite? <Link className="font-semibold text-indigo-800 underline" href="/entrar">Entrar</Link></>}>
      <p className="text-slate-800">O AutoClip IA está em <strong>beta fechado</strong>. O acesso é feito por convite:
        quem é convidado recebe um e-mail com o link para criar a senha.</p>
    </AuthCard>
  );
}

export default function Cadastro() {
  if (BETA_MODE) return <ConvitePendente />;
  return <CadastroAberto />;
}

function CadastroAberto() {
  const { signUp } = useAuth();
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
    setError(null);
    const err = await signUp(String(f.get("email")), senha);
    setBusy(false);
    if (err) setError(err);
    else setDone(true);
  }

  return (
    <AuthCard title="Criar conta" footer={<>Já tem conta? <Link className="font-semibold text-indigo-800 underline" href="/entrar">Entrar</Link></>}>
      {done ? <Alert kind="success">Conta criada. Enviamos um link de confirmação para o seu e-mail.</Alert> : (
        <form onSubmit={onSubmit} className="space-y-4">
          {error && <Alert>{error}</Alert>}
          <Field id="email" name="email" type="email" label="E-mail" autoComplete="email" required />
          <Field id="senha" name="senha" type="password" label="Senha" autoComplete="new-password" minLength={8}
            hint="Mínimo de 8 caracteres." required />
          <Field id="confirmacao" name="confirmacao" type="password" label="Confirme a senha" autoComplete="new-password" required />
          <p className="text-sm text-slate-600">Ao criar a conta, você concorda em enviar apenas vídeos que tem
            direito de processar. Veja <Link className="underline" href="/privacidade">como tratamos seus dados</Link>.</p>
          <Button type="submit" disabled={busy} className="w-full">{busy ? "Criando…" : "Criar conta"}</Button>
        </form>
      )}
    </AuthCard>
  );
}
