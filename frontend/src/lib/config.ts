// Variáveis públicas (incorporadas no build). Nunca coloque segredos em NEXT_PUBLIC_*.
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";
// "test" só para testes automatizados locais: usa um token injetado pelo teste. Nunca em produção.
export const AUTH_MODE: "supabase" | "test" = process.env.NEXT_PUBLIC_AUTH_MODE === "test" ? "test" : "supabase";
export const TEST_TOKEN_KEY = "autoclip_test_token";
// Beta fechado: cadastro só por convite (a API também recusa e-mails não convidados).
export const BETA_MODE = process.env.NEXT_PUBLIC_BETA_MODE === "true";
