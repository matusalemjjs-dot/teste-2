import Link from "next/link";

export default function NaoEncontrada() {
  return (
    <main id="conteudo" className="mx-auto max-w-xl px-4 py-16 text-center">
      <h1 className="text-2xl font-bold">Página não encontrada</h1>
      <p className="mt-3 text-slate-700">O endereço pode estar errado ou a página não existe mais.</p>
      <Link href="/" className="mt-6 inline-block min-h-11 rounded-lg bg-indigo-700 px-4 py-2 font-semibold text-white
        hover:bg-indigo-800">Voltar ao início</Link>
    </main>
  );
}
