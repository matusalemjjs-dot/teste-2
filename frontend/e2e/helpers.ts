import { type Page, expect } from "@playwright/test";
import { SignJWT } from "jose";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { E2E_JWT_SECRET } from "../playwright.config";

export const ARTIFACTS = path.resolve(__dirname, "../../backend/tests/e2e/.artifacts");
export const VIDEO = path.join(ARTIFACTS, "fala.mp4");
export const NOT_VIDEO = path.join(ARTIFACTS, "nao-e-video.mp4");

/** Token igual ao do Supabase (HS256), assinado com o segredo local da pilha de teste. */
export async function token(email: string, sub = randomUUID()): Promise<string> {
  return new SignJWT({ email, role: "authenticated" })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(sub).setAudience("authenticated").setIssuer("https://e2e-local.supabase.co/auth/v1")
    .setIssuedAt().setExpirationTime("2h")
    .sign(new TextEncoder().encode(E2E_JWT_SECRET));
}

export async function login(page: Page, email = `usuario-${randomUUID().slice(0, 8)}@exemplo.com`) {
  const t = await token(email);
  // injeta uma única vez por aba (sair da conta precisa de fato remover o token)
  await page.addInitScript((value) => {
    if (!window.sessionStorage.getItem("autoclip_test_injected")) {
      window.localStorage.setItem("autoclip_test_token", value);
      window.sessionStorage.setItem("autoclip_test_injected", "1");
    }
  }, t);
  return { email, token: t };
}

export async function novoProjeto(page: Page, titulo: string) {
  await page.goto("/projetos/novo");
  await page.getByLabel("Título do projeto").fill(titulo);
  await page.getByLabel(/Confirmo que tenho autorização/).check();
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByRole("heading", { name: titulo })).toBeVisible();
}
