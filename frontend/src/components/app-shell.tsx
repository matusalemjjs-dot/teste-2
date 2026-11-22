"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth";

export function TestModeBanner() {
  const { testMode } = useAuth();
  if (!testMode) return null;
  return <div className="bg-amber-300 px-4 py-1 text-center text-sm font-semibold text-amber-950">
    Modo de teste — autenticação simulada para testes automatizados. Não use em produção.</div>;
}

export function Header() {
  const { token, email, signOut } = useAuth();
  const router = useRouter();
  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <Link href={token ? "/painel" : "/"} className="text-lg font-bold text-indigo-800">AutoClip IA</Link>
        {token && (
          <nav aria-label="Principal" className="flex flex-wrap items-center gap-4 text-sm">
            <Link href="/painel" className="font-medium text-slate-800 hover:text-indigo-700">Painel</Link>
            <Link href="/projetos/novo" className="font-medium text-slate-800 hover:text-indigo-700">Novo projeto</Link>
            {email && <span className="hidden text-slate-600 sm:inline">{email}</span>}
            <button type="button" onClick={async () => { await signOut(); router.push("/entrar"); }}
              className="font-medium text-slate-800 underline-offset-4 hover:text-indigo-700 hover:underline">Sair</button>
          </nav>
        )}
      </div>
    </header>
  );
}

/** Protege rotas no navegador. A API valida o token em toda requisição de qualquer forma. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { ready, token } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  useEffect(() => {
    if (ready && !token) router.replace(`/entrar?voltar=${encodeURIComponent(pathname)}`);
  }, [ready, token, router, pathname]);
  if (!ready || !token) return <p className="p-8 text-center text-slate-600" role="status">Carregando…</p>;
  return <>{children}</>;
}

export function Page({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main id="conteudo" className="mx-auto max-w-5xl px-4 py-8">
      <h1 className="mb-6 text-2xl font-bold text-slate-900">{title}</h1>
      {children}
    </main>
  );
}
