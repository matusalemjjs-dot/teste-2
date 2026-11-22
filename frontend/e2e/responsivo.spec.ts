import { expect, test } from "@playwright/test";
import { login } from "./helpers";

for (const rota of ["/", "/entrar", "/painel", "/projetos/novo"]) {
  test(`sem rolagem horizontal no celular: ${rota}`, async ({ page }) => {
    await login(page);
    await page.goto(rota);
    await expect(page.getByRole("heading").first()).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/telas/celular${rota.replace(/\//g, "-") || "-inicio"}.png` });
  });
}
