import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login, novoProjeto } from "./helpers";

async function semViolacoesGraves(page: import("@playwright/test").Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const graves = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(graves.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

for (const rota of ["/", "/entrar", "/cadastro", "/recuperar-senha"]) {
  test(`acessibilidade (WCAG A/AA) em ${rota}`, async ({ page }) => {
    await page.goto(rota);
    await semViolacoesGraves(page);
  });
}

test("acessibilidade no painel e no novo projeto", async ({ page }) => {
  await login(page);
  await page.goto("/painel");
  await expect(page.getByRole("heading", { name: "Painel" })).toBeVisible();
  await semViolacoesGraves(page);
  await novoProjeto(page, "Acessível");
  await semViolacoesGraves(page);
});
