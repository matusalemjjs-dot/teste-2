import { expect, test } from "@playwright/test";
import { login } from "./helpers";

test("rota protegida redireciona para o login", async ({ page }) => {
  await page.goto("/painel");
  await expect(page).toHaveURL(/\/entrar\?voltar=%2Fpainel/);
  await expect(page.getByRole("heading", { name: "Entrar" })).toBeVisible();
  await expect(page.getByText("Modo de teste — autenticação simulada")).toBeVisible();
});

test("página inicial em português com links de acesso", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("lang", "pt-BR");
  await expect(page.getByText("Beta fechado — acesso por convite")).toBeVisible();
  await expect(page.getByRole("link", { name: "Criar conta" })).toHaveCount(0);
  await page.getByRole("link", { name: "Entrar" }).first().click();
  await expect(page).toHaveURL(/\/entrar/);
});

test("usuário autenticado vê o painel vazio e consegue sair", async ({ page }) => {
  const { email } = await login(page);
  await page.goto("/");
  await expect(page).toHaveURL(/\/painel/);
  await expect(page.getByText("Você ainda não tem projetos.")).toBeVisible();
  await expect(page.getByTestId("stat-processados")).toHaveText("0");
  await expect(page.getByText(email)).toBeVisible();
  await page.getByRole("button", { name: "Sair" }).click();
  await expect(page).toHaveURL(/\/entrar/);
});

test("token inválido não acessa dados", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem("autoclip_test_token", "eyJhbGciOiJIUzI1NiJ9.e30.x"));
  await page.goto("/painel");
  await expect(page.getByRole("main").getByRole("alert")).toContainText(/Token|Sessão|inválid/i);
});

test("beta fechado: cadastro mostra acesso por convite", async ({ page }) => {
  await page.goto("/cadastro");
  await expect(page.getByRole("heading", { name: "Acesso por convite" })).toBeVisible();
  await expect(page.getByLabel("Senha")).toHaveCount(0);
  await page.goto("/entrar");
  await expect(page.getByText("Beta fechado: o acesso é feito por convite.")).toBeVisible();
});

test("beta fechado: e-mail não convidado não acessa dados", async ({ page }) => {
  await login(page, "intruso@outro-dominio.com");
  await page.goto("/painel");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("beta fechado");
});
