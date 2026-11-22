# AutoClip IA — frontend

Next.js 16 (App Router) + React 19 + TypeScript + Tailwind CSS 4. Interface em português, responsiva, com
autenticação pelo Supabase Auth. Consome a API do backend (`../backend`). Veja o README da raiz.

```bash
npm install
cp .env.example .env.local      # Windows: Copy-Item .env.example .env.local
npm run dev                     # http://localhost:3000
npm run lint && npm run typecheck
npm run test:e2e                # Playwright: sobe a pilha completa sozinho (veja abaixo)
```

Telas: `/` · `/entrar` · `/cadastro` · `/recuperar-senha` · `/redefinir-senha` · `/painel` · `/projetos/novo` ·
`/projetos/[id]` (progresso e galeria).
