"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { BETA_MODE } from "@/lib/config";

function AvisoContaExcluida() {
  const aviso = useSearchParams().get("conta_excluida");
  if (!aviso) return null;
  return <div role="status" className="mb-8 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm
    text-emerald-900">{aviso}</div>;
}

export default function Home() {
  const { ready, token } = useAuth();
  const router = useRouter();
  useEffect(() => { if (ready && token) router.replace("/painel"); }, [ready, token, router]);
  return (
    <main id="conteudo" className="mx-auto max-w-3xl px-4 py-16 text-center">
      <Suspense fallback={null}><AvisoContaExcluida /></Suspense>
      <h1 className="text-3xl font-bold sm:text-4xl">Transforme vídeos longos em cortes curtos</h1>
      <p className="mx-auto mt-4 max-w-xl text-lg text-slate-700">
        Envie um vídeo de até 60 minutos. O AutoClip IA transcreve a fala, encontra os melhores momentos e gera
        cortes verticais com legendas, prontos para baixar.</p>
      {BETA_MODE && <p className="mt-6 inline-block rounded-full bg-amber-100 px-4 py-1 text-sm font-semibold text-amber-900">
        Beta fechado — acesso por convite</p>}
      <div className="mt-8 flex flex-wrap justify-center gap-3">
        {!BETA_MODE && <Link href="/cadastro" className="rounded-lg bg-indigo-700 px-5 py-3 font-semibold text-white
          hover:bg-indigo-800">Criar conta</Link>}
        <Link href="/entrar" className="rounded-lg px-5 py-3 font-semibold text-slate-900 ring-1 ring-slate-300 hover:bg-white">
          Entrar</Link>
      </div>
    </main>
  );
}
