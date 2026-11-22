import type { Metadata } from "next";
import { Footer, Header, TestModeBanner } from "@/components/app-shell";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "AutoClip IA",
  description: "Transforme vídeos longos em cortes curtos com inteligência artificial.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="pt-BR">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <a href="#conteudo" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50
          focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:shadow">Pular para o conteúdo</a>
        <AuthProvider>
          <TestModeBanner />
          <Header />
          {children}
          <Footer />
        </AuthProvider>
      </body>
    </html>
  );
}
