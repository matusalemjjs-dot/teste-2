import type { NextConfig } from "next";

// Barreira: o modo de login de teste só pode ser compilado/servido com liberação explícita (testes E2E).
if (process.env.NEXT_PUBLIC_AUTH_MODE === "test" && process.env.AUTOCLIP_ALLOW_TEST_AUTH !== "1") {
  throw new Error(
    "NEXT_PUBLIC_AUTH_MODE=test é só para testes automatizados. Para usá-lo, defina AUTOCLIP_ALLOW_TEST_AUTH=1; " +
      "em produção, remova NEXT_PUBLIC_AUTH_MODE.",
  );
}

const origem = (url: string | undefined) => {
  try {
    return url ? new URL(url).origin : "";
  } catch {
    return "";
  }
};
const API = origem(process.env.NEXT_PUBLIC_API_URL) || "http://localhost:8000";
const SUPABASE = origem(process.env.NEXT_PUBLIC_SUPABASE_URL);
const STORAGE = origem(process.env.NEXT_PUBLIC_STORAGE_ORIGIN); // ex.: https://bucket.r2.cloudflarestorage.com

// CSP em modo SÓ OBSERVAÇÃO (Report-Only): nada é bloqueado; violações vão para a API (/api/v1/csp-report).
// Scripts com 'unsafe-inline' (caminho "a" do plano): o Next.js usa scripts inline nas páginas estáticas.
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${STORAGE}`,
  `media-src 'self' blob: ${STORAGE}`,
  `connect-src 'self' ${API} ${SUPABASE} ${STORAGE}`,
  "font-src 'self' data:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  `report-uri ${API}/api/v1/csp-report`,
].map((d) => d.replace(/\s+/g, " ").trim()).join("; ");

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy-Report-Only", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
