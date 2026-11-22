"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { Alert, Card } from "./ui";

export function AuthCard({ title, children, footer }: { title: string; children: React.ReactNode; footer?: React.ReactNode }) {
  const { configured, testMode } = useAuth();
  return (
    <main id="conteudo" className="mx-auto max-w-md px-4 py-12">
      <Card>
        <h1 className="mb-5 text-2xl font-bold">{title}</h1>
        {!configured && <div className="mb-4"><Alert>A autenticação não está configurada. Defina
          NEXT_PUBLIC_SUPABASE_URL e NEXT_PUBLIC_SUPABASE_ANON_KEY.</Alert></div>}
        {testMode && <div className="mb-4"><Alert kind="info">Modo de teste ativo: o login é feito pelos testes
          automatizados. <Link className="underline" href="/painel">Ir para o painel</Link></Alert></div>}
        {children}
      </Card>
      {footer && <div className="mt-4 text-center text-sm text-slate-700">{footer}</div>}
    </main>
  );
}
