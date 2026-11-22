import { expect, test } from "@playwright/test";
import { login, novoProjeto } from "./helpers";

test("página de privacidade mostra prazos e suboperadores reais da configuração", async ({ page }) => {
  await page.goto("/privacidade");
  const prazos = page.getByTestId("prazos");
  await expect(prazos).toContainText("7 dias");
  await expect(prazos).toContainText("30 dias");
  await expect(page.getByText(/Anthropic/).first()).toBeVisible();
  await expect(page.getByText(/texto jurídico definitivo ainda está em revisão/)).toBeVisible();
  await expect(page.getByTestId("contato-pendente")).toBeVisible(); // sem SUPPORT_EMAIL na pilha de teste
});

test("rodapé leva à página de privacidade", async ({ page }) => {
  await page.goto("/entrar");
  await page.getByRole("link", { name: "Como tratamos seus dados" }).last().click();
  await expect(page).toHaveURL(/\/privacidade/);
});

test("excluir conta pela interface apaga tudo e bloqueia o token antigo", async ({ page }) => {
  const { token } = await login(page);
  await novoProjeto(page, "Projeto que será apagado");
  await page.getByRole("link", { name: "Minha conta" }).click();
  const botao = page.getByRole("button", { name: "Excluir minha conta" });
  await expect(botao).toBeDisabled();
  await page.getByLabel(/Para confirmar, digite EXCLUIR/).fill("excluir");
  await expect(botao).toBeDisabled(); // exige o texto exato
  await page.getByLabel(/Para confirmar, digite EXCLUIR/).fill("EXCLUIR");
  await botao.click();
  await expect(page).toHaveURL(/\/\?conta_excluida=/);
  await expect(page.getByRole("status")).toContainText("Seus dados foram apagados");
  expect(await page.evaluate(() => window.localStorage.getItem("autoclip_test_token"))).toBeNull(); // saiu
  // o mesmo token (ainda válido) não recria a conta
  await page.evaluate((t) => window.localStorage.setItem("autoclip_test_token", t), token);
  await page.goto("/painel");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Esta conta foi excluída");
});
