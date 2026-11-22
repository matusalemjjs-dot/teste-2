"use client"; // limites de erro precisam ser componentes de cliente

import Link from "next/link";
import { useEffect } from "react";
import { reportClientError } from "@/lib/report";

export default function Erro({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    const e = error instanceof Error ? error : new Error(String(error));
    reportClientError(e.message, e.stack, "error-boundary");
  }, [error]);
  return (
    <main id="conteudo" className="mx-auto max-w-xl px-4 py-16 text-center">
      <h1 className="text-2xl font-bold">Algo deu errado</h1>
      <p className="mt-3 text-slate-700">Ocorreu um erro inesperado nesta página. O problema foi registrado.</p>
      <div className="mt-6 flex justify-center gap-3">
        <button type="button" onClick={() => retry()}
          className="min-h-11 rounded-lg bg-indigo-700 px-4 py-2 font-semibold text-white hover:bg-indigo-800">
          Tentar novamente</button>
        <Link href="/painel" className="min-h-11 rounded-lg px-4 py-2 font-semibold ring-1 ring-slate-300">
          Ir para o painel</Link>
      </div>
    </main>
  );
}
