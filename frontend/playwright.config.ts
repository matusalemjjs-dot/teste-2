import { defineConfig, devices } from "@playwright/test";

// Testes de ponta a ponta contra a pilha real (API, worker, Redis, S3 emulado, Postgres).
// A pilha é iniciada por backend/tests/e2e/stack.py; o frontend roda em modo de teste de autenticação.
// Com E2E_BASE_URL e E2E_API_URL, roda contra uma pilha já no ar (ex.: a pilha de produção local, docs/IMPLANTACAO.md).
export const E2E_JWT_SECRET = "segredo-e2e-apenas-local-com-32-bytes-ou-mais";
const EXTERNA = Boolean(process.env.E2E_BASE_URL);
const WEB = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
export const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8100";

export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: WEB, trace: "off", locale: "pt-BR", acceptDownloads: true, ignoreHTTPSErrors: EXTERNA },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] }, testIgnore: /responsivo/ },
    { name: "celular", use: { ...devices["Pixel 7"] }, testMatch: /responsivo/ },
  ],
  webServer: EXTERNA ? undefined : [
    {
      command: "cd ../backend && .venv/bin/python -m tests.e2e.stack",
      url: `${API}/health`,
      timeout: 300_000,
      reuseExistingServer: false,
      env: { E2E_JWT_SECRET, E2E_WEB_ORIGIN: WEB },
      stdout: "pipe",
    },
    {
      command: "npm run build:e2e && npx next start -p 3100 -H 127.0.0.1",
      url: WEB,
      timeout: 300_000,
      reuseExistingServer: false,
      env: { NEXT_PUBLIC_API_URL: API, NEXT_PUBLIC_AUTH_MODE: "test", NEXT_PUBLIC_BETA_MODE: "true",
        NEXT_PUBLIC_STORAGE_ORIGIN: "http://127.0.0.1:5101", AUTOCLIP_ALLOW_TEST_AUTH: "1",
        NEXT_TELEMETRY_DISABLED: "1" },
    },
  ],
});
