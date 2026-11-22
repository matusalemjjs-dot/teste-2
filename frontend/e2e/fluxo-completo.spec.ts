import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import { API } from "../playwright.config";
import { login, NOT_VIDEO, novoProjeto, VIDEO } from "./helpers";

test("envio, processamento real, galeria, reprodução, download e regeneração", async ({ page }) => {
  const violacoesCsp: string[] = [];
  page.on("console", (m) => { if (/Content.Security.Policy/i.test(m.text())) violacoesCsp.push(m.text()); });
  await login(page);
  await novoProjeto(page, "Dicas de inglês");
  await page.getByLabel(/Vídeo \(MP4/).setInputFiles(VIDEO);
  await page.getByRole("button", { name: "Enviar e processar" }).click();

  // upload direto ao storage -> validação -> fila -> página do projeto acompanhando o progresso
  await expect(page).toHaveURL(/\/projetos\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  await expect(page.getByText("Concluído", { exact: true })).toBeVisible({ timeout: 200_000 });
  await expect(page.getByText(/cortes gerados/)).toBeVisible();
  await expect(page.getByTestId("retencao")).toContainText("O vídeo original será apagado em");
  await expect(page.getByTestId("retencao")).toContainText("O projeto e os cortes serão apagados em");

  const cards = page.getByTestId("clip");
  await expect(cards.first()).toBeVisible();
  const total = await cards.count();
  expect(total).toBeGreaterThan(0);
  const card = cards.first();
  await expect(card.getByText(/Nota estimada \d+\/100/)).toBeVisible();
  await expect(card.getByRole("img", { name: /Miniatura do corte/ })).toBeVisible();
  await expect(card.getByText("Avaliação de teste.")).toBeVisible(); // justificativa (avaliador de teste no E2E)
  await page.screenshot({ path: "test-results/telas/galeria.png", fullPage: true });
  // acessibilidade da página do projeto com a galeria
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
  // avaliação do corte persiste
  await card.getByRole("button", { name: "Útil", exact: true }).click();
  await expect(card.getByRole("button", { name: "Útil", exact: true })).toHaveAttribute("aria-pressed", "true");

  await page.reload();
  await expect(page.getByTestId("clip").first().getByRole("button", { name: "Útil", exact: true }))
    .toHaveAttribute("aria-pressed", "true");
  // reprodução: o navegador carrega o MP4 real (1080x1920)
  await card.getByRole("button", { name: "Assistir" }).click();
  const video = card.locator("video");
  await expect(video).toBeVisible();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState), { timeout: 30_000 }).toBeGreaterThan(0);
  const dims = await video.evaluate((v: HTMLVideoElement) => [v.videoWidth, v.videoHeight, v.duration]);
  expect(dims[0]).toBe(1080);
  expect(dims[1]).toBe(1920);
  expect(dims[2]).toBeGreaterThanOrEqual(15);
  expect(dims[2]).toBeLessThanOrEqual(60.5);

  // download do MP4
  const [download] = await Promise.all([page.waitForEvent("download"), card.getByRole("button", { name: "Baixar MP4" }).click()]);
  expect(download.suggestedFilename()).toMatch(/\.mp4$/);
  const file = await download.path();
  expect(fs.statSync(file).size).toBeGreaterThan(10_000);
  expect(fs.readFileSync(file).subarray(4, 8).toString()).toBe("ftyp");

  // regeneração com fundo desfocado
  await card.getByRole("button", { name: "Regenerar" }).click();
  await card.getByLabel("Enquadramento").selectOption("blur_background");
  await card.getByRole("button", { name: "Gerar nova versão" }).click();
  await expect(page.getByTestId("clip").first().getByText("fundo desfocado")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("Gerando nova versão…")).toHaveCount(0, { timeout: 120_000 });

  // painel reflete o consumo
  await page.getByRole("link", { name: "Painel" }).click();
  await expect(page.getByTestId("stat-processados")).toHaveText("1");
  await expect(page.getByTestId("stat-minutos")).not.toHaveText(/^0 \//);
  await page.screenshot({ path: "test-results/telas/painel.png", fullPage: true });
  // CSP em modo observação: nenhuma violação durante todo o fluxo (upload, vídeo, miniaturas, download)
  expect(violacoesCsp).toEqual([]);
});

test("arquivo que não é vídeo é recusado com mensagem clara", async ({ page }) => {
  await login(page);
  await novoProjeto(page, "Arquivo inválido");
  await page.getByLabel(/Vídeo \(MP4/).setInputFiles(NOT_VIDEO);
  await page.getByRole("button", { name: "Enviar e processar" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("não é um vídeo MP4 válido");
  // pode tentar de novo na mesma tela
  await expect(page.getByRole("button", { name: "Enviar e processar" })).toBeEnabled();
});

test("arquivo com extensão errada é recusado antes do envio", async ({ page }) => {
  await login(page);
  await novoProjeto(page, "Extensão errada");
  await page.getByLabel(/Vídeo \(MP4/).setInputFiles({ name: "video.mov", mimeType: "video/quicktime", buffer: Buffer.from("x") });
  await page.getByRole("button", { name: "Enviar e processar" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Selecione um arquivo .mp4");
});

test("criar projeto exige confirmação de direitos", async ({ page }) => {
  await login(page);
  await page.goto("/projetos/novo");
  await page.getByLabel("Título do projeto").fill("Sem confirmação");
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page).toHaveURL(/\/projetos\/novo$/);
  const valid = await page.getByLabel(/Confirmo que tenho autorização/).evaluate((el: HTMLInputElement) => el.validity.valid);
  expect(valid).toBe(false);
});

test("usuário não acessa projeto de outro usuário", async ({ browser }) => {
  const a = await browser.newPage();
  await login(a);
  await novoProjeto(a, "Projeto privado");
  await a.goto("/painel");
  await a.getByRole("link", { name: /Projeto privado/ }).click();
  await expect(a).toHaveURL(/\/projetos\/[0-9a-f-]{36}$/);
  const url = a.url();
  await a.close();
  const b = await browser.newPage();
  await login(b);
  await b.goto(url);
  await expect(b.getByRole("heading", { name: "Projeto não encontrado" })).toBeVisible();
  await b.close();
});

test("cancelar e excluir projeto", async ({ page }) => {
  await login(page);
  await novoProjeto(page, "Para excluir");
  await page.goto("/painel");
  await page.getByRole("link", { name: /Para excluir/ }).click();
  await page.getByRole("button", { name: "Cancelar" }).click();
  await expect(page.getByText("Processamento cancelado.")).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Excluir projeto" }).click();
  await expect(page).toHaveURL(/\/painel/);
  await expect(page.getByText("Você ainda não tem projetos.")).toBeVisible();
});

test("cabeçalhos de segurança e CSP em modo observação", async ({ page }) => {
  const r = await page.goto("/");
  const h = r!.headers();
  expect(h["x-frame-options"]).toBe("DENY");
  expect(h["x-content-type-options"]).toBe("nosniff");
  expect(h["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(h["content-security-policy"]).toBeUndefined(); // ainda não bloqueia
  const csp = h["content-security-policy-report-only"];
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).toContain(`connect-src 'self' ${API}`);
  expect(csp).toContain(`report-uri ${API}/api/v1/csp-report`);
});

test("página não encontrada em português", async ({ page }) => {
  await page.goto("/rota-que-nao-existe");
  await expect(page.getByRole("heading", { name: "Página não encontrada" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "pt-BR");
});

test("controle: o detector de violações de CSP registra uma origem não autorizada", async ({ page }) => {
  const violacoes: string[] = [];
  page.on("console", (m) => { if (/Content.Security.Policy/i.test(m.text())) violacoes.push(m.text()); });
  await page.goto("/");
  await page.evaluate(() => { const i = new Image(); i.src = "http://127.0.0.1:9/nao-autorizada.png"; document.body.append(i); });
  await expect.poll(() => violacoes.length).toBeGreaterThan(0);
  expect(violacoes[0]).toMatch(/img-src/);
});
