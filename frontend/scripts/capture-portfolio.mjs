import { chromium } from "playwright";
import { access, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const frontendRoot = path.resolve(import.meta.dirname, "..");
const repositoryRoot = path.resolve(frontendRoot, "..");
const runtimeDir = path.join(repositoryRoot, ".runtime", "portfolio-capture");
const baseURL = process.env.PORTFOLIO_BASE_URL || "http://127.0.0.1:4173";
const productionCapture = new URL(baseURL).hostname === "bitcoinbot.uk";
const chromeCandidates = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
];
const thesis = "BTC 4H volume ratio >= 1.2 and price above MA200. What happened over the next 4H, 12H and 24H historically?";

async function available(pathname) {
  try { await access(pathname); return true; } catch { return false; }
}

async function launchBrowser() {
  const executablePath = (await Promise.all(chromeCandidates.map(async item => [item, await available(item)])))
    .find(([, exists]) => exists)?.[0];
  return chromium.launch({ headless: true, executablePath });
}

const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function preparePage(page, pathname = "/", language = "en") {
  await page.addInitScript(value => localStorage.setItem("crypto-bot-language", value), language);
  await page.goto(new URL(pathname, baseURL).toString(), { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.locator("main").first().waitFor({ state: "visible", timeout: 60000 });
  await page.addStyleTag({ content: `
    *, *::before, *::after { animation-duration: 0s !important; transition-duration: 0s !important; }
    html { scroll-behavior: auto !important; }
  ` });
  await sleep(productionCapture ? 1800 : 500);
  await assertPrivacy(page);
}

async function assertPrivacy(page) {
  const visibleText = await page.locator("body").innerText();
  const prohibited = [
    /ADMIN_TOKEN/i,
    /authorization:\s*bearer/i,
    /[A-Z]:\\Users\\/i,
    /(?:THESIS|DEEPSEEK|PARSER|EXPLANATION)_[A-Z_]*(?:KEY|PATH|SHA256)\s*[:=]/i,
    /ssh-rsa|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY/i,
    /data_cache\/[\w.-]+\.db/i,
  ];
  if (productionCapture) prohibited.push(/localhost|127\.0\.0\.1|Paper API (?:offline|is not running)/i);
  for (const pattern of prohibited) {
    if (pattern.test(visibleText)) throw new Error(`Privacy audit failed: visible text matched ${pattern}`);
  }
}

async function configureCaseA(page) {
  await page.getByLabel("Test an idea").fill(thesis);
  await page.getByRole("button", { name: "Build manually" }).click();
  await page.getByRole("combobox", { name: "Instrument" }).selectOption("BTC");
  await page.getByRole("combobox", { name: "Timeframe" }).selectOption("4H");
  await page.getByRole("button", { name: "Add condition" }).click();
  await page.getByRole("combobox", { name: "Feature 1" }).selectOption("VOLUME_RATIO");
  await page.getByRole("combobox", { name: "Operator 1" }).selectOption("gte");
  await page.getByLabel("Threshold 1").fill("1.2");
  await page.getByRole("button", { name: "Add condition" }).click();
  await page.getByRole("combobox", { name: "Feature 2" }).selectOption("PRICE_ABOVE_MA200");
}

async function captureProduct(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  page.setDefaultTimeout(90000);

  await preparePage(page);
  await page.getByRole("heading", { name: "Evidence, not predictions." }).waitFor();
  await page.screenshot({ path: path.join(runtimeDir, "home.png") });

  await preparePage(page, "/test-an-idea");
  await configureCaseA(page);
  await page.getByRole("button", { name: "Run historical test" }).click();
  const evidence = page.getByRole("heading", { name: "Historical evidence" }).first();
  await evidence.waitFor({ timeout: 60000 });
  await page.getByText("346", { exact: true }).first().waitFor({ timeout: 60000 });
  await evidence.evaluate(element => element.scrollIntoView({ block: "start" }));
  await page.evaluate(() => window.scrollBy(0, -86));
  await sleep(1000);
  await assertPrivacy(page);
  await page.screenshot({ path: path.join(runtimeDir, "test-result.png") });

  await page.getByRole("button", { name: "View event" }).first().click();
  const eventChart = page.getByLabel("Historical event candlestick evidence");
  await eventChart.waitFor({ timeout: 30000 });
  await eventChart.evaluate(element => element.scrollIntoView({ block: "center" }));
  await sleep(800);
  await page.screenshot({ path: path.join(runtimeDir, "evidence-chart.png") });

  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Track this thesis" }).click();
  const trackingLink = page.getByRole("link", { name: "View tracking" });
  await trackingLink.waitFor({ timeout: 30000 });
  const trackedHref = await trackingLink.getAttribute("href");
  if (!trackedHref) throw new Error("Tracking detail link was not produced");

  await preparePage(page, "/tracking");
  await page.getByRole("heading", { name: "What I'm tracking" }).waitFor();
  await page.locator(".tracking-card").first().waitFor();
  await page.screenshot({ path: path.join(runtimeDir, "tracking.png") });

  await preparePage(page, "/what-changed");
  await page.getByRole("heading", { name: "What changed?" }).waitFor();
  await page.screenshot({ path: path.join(runtimeDir, "what-changed.png") });

  await preparePage(page, trackedHref);
  await page.getByRole("button", { name: "Archive" }).click();
  await page.waitForURL(/\/tracking$/, { timeout: 30000 });
  await writeFile(path.join(runtimeDir, "capture.json"), JSON.stringify({ thesis, trackedHref, archived: true }, null, 2));
  await context.close();
}

async function validateMobile(browser, language) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  await preparePage(page, "/", language);
  await page.locator("h1").first().waitFor();
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    bodyWidth: document.body.getBoundingClientRect().width,
  }));
  if (dimensions.clientWidth !== 390 || dimensions.scrollWidth > dimensions.clientWidth) {
    throw new Error(`Mobile layout overflow: ${JSON.stringify(dimensions)}`);
  }
  await page.screenshot({ path: path.join(runtimeDir, `home-mobile-${language}.png`) });
  await writeFile(path.join(runtimeDir, `mobile-${language}.json`), JSON.stringify(dimensions, null, 2));
  await context.close();
}

await mkdir(runtimeDir, { recursive: true });
const browser = await launchBrowser();
try {
  await captureProduct(browser);
  await validateMobile(browser, "en");
  await validateMobile(browser, "zh");
  process.stdout.write("Production product capture and 390px acceptance complete.\n");
} finally {
  await browser.close();
}
