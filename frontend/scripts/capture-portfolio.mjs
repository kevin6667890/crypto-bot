import { chromium } from "playwright";
import { access, mkdir } from "node:fs/promises";
import path from "node:path";

const frontendRoot = path.resolve(import.meta.dirname, "..");
const repositoryRoot = path.resolve(frontendRoot, "..");
const runtimeDir = path.join(repositoryRoot, ".runtime", "portfolio-capture");
const baseURL = process.env.PORTFOLIO_BASE_URL || "http://127.0.0.1:4173";
const chromeCandidates = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
];

async function available(pathname) {
  try { await access(pathname); return true; } catch { return false; }
}

async function launchBrowser() {
  const executablePath = (await Promise.all(chromeCandidates.map(async item => [item, await available(item)])))
    .find(([, exists]) => exists)?.[0];
  return chromium.launch({ headless: true, executablePath });
}

const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function preparePage(page) {
  await page.addInitScript(() => localStorage.setItem("crypto-bot-language", "en"));
  await page.goto(baseURL, { waitUntil: "domcontentloaded" });
  await page.locator('[data-route="workspace"]').waitFor({ state: "visible" });
  await page.locator(".workspace-chart").waitFor({ state: "visible" });
  await page.locator(".scan-row").first().waitFor({ state: "visible", timeout: 20000 });
  await page.addStyleTag({ content: `
    *, *::before, *::after { animation-duration: 0s !important; transition-duration: 0s !important; }
    html { scroll-behavior: auto !important; }
    .market-flow-coverage { display: none !important; }
  ` });
  await sleep(700);
  await page.evaluate(() => window.scrollTo(0, 0));
  await assertPrivacy(page);
}

async function assertPrivacy(page) {
  const text = await page.locator("body").innerText();
  const prohibited = [
    /ADMIN_TOKEN/i,
    /authorization:\s*bearer/i,
    /[A-Z]:\\Users\\/i,
    /\.env(?:\s|$)/i,
    /ssh-rsa|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY/i,
  ];
  for (const pattern of prohibited) {
    if (pattern.test(text)) throw new Error(`Privacy audit failed: visible text matched ${pattern}`);
  }
}

async function openPrimary(page, name, route) {
  await page.getByRole("button", { name, exact: true }).click();
  await page.locator(`[data-route="${route}"]`).waitFor({ state: "visible" });
  await page.evaluate(() => window.scrollTo(0, 0));
}

async function openOptimization(page) {
  await openPrimary(page, "Research", "research");
  const overview = page.getByRole("button", { name: "Research & Validation", exact: true });
  if (await overview.isVisible()) await overview.click();
  await page.locator('[data-research-view="overview"]').waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Optimization Lab", exact: true }).click();
  await page.getByRole("heading", { name: "Experiment Families", exact: true }).waitFor({ state: "visible" });
  await page.locator(".research-table-wrap tbody tr").first().waitFor({ state: "visible" });
  await page.evaluate(() => window.scrollTo(0, 0));
}

async function openDecisionTrace(page) {
  await openPrimary(page, "Research", "research");
  await page.getByRole("button", { name: "Decision Trace", exact: true }).click();
  await page.locator("[data-strategy-router-page]").waitFor({ state: "visible" });
  await page.locator(".router-no-trade").waitFor({ state: "visible" });
  await page.evaluate(() => window.scrollTo(0, 0));
}

async function openLifecycle(page) {
  await openPrimary(page, "Research", "research");
  const overview = page.getByRole("button", { name: "Research & Validation", exact: true });
  if (await overview.isVisible()) await overview.click();
  await page.locator('[data-research-view="overview"]').waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Strategy Lifecycle", exact: true }).click();
  await page.getByRole("heading", { name: "Strategy Lifecycle", exact: true }).waitFor({ state: "visible" });
  const candidates = page.locator(".lifecycle-row");
  if (await candidates.count() > 0) {
    await candidates.first().click();
    await page.locator(".lifecycle-row.active").waitFor({ state: "visible" });
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

async function addTitleCard(page, title, subtitle) {
  await page.evaluate(({ title, subtitle }) => {
    document.getElementById("portfolio-title-card")?.remove();
    const card = document.createElement("section");
    card.id = "portfolio-title-card";
    Object.assign(card.style, {
      position: "fixed", inset: "0", zIndex: "2147483647", display: "grid",
      placeContent: "center", textAlign: "center", color: "#10251f",
      background: "linear-gradient(135deg, #f8fbfa 0%, #e8f5f1 100%)",
      fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
    });
    const heading = document.createElement("h1");
    heading.textContent = title;
    Object.assign(heading.style, { fontSize: "48px", margin: "0 0 16px", letterSpacing: "-1.5px" });
    const paragraph = document.createElement("p");
    paragraph.textContent = subtitle;
    Object.assign(paragraph.style, { fontSize: "22px", margin: "0", color: "#416158" });
    card.append(heading, paragraph);
    document.body.append(card);
  }, { title, subtitle });
}

async function removeTitleCard(page) {
  await page.evaluate(() => document.getElementById("portfolio-title-card")?.remove());
}

async function captureScreenshots(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  await preparePage(page);
  await sleep(800);
  await page.screenshot({ path: path.join(runtimeDir, "workspace.png") });

  await openPrimary(page, "Market", "market");
  await page.locator("[data-market-state-page]").waitFor({ state: "visible" });
  await sleep(500);
  await page.screenshot({ path: path.join(runtimeDir, "market.png") });

  await openOptimization(page);
  await sleep(500);
  await page.screenshot({ path: path.join(runtimeDir, "research.png") });

  await openDecisionTrace(page);
  await sleep(500);
  await page.screenshot({ path: path.join(runtimeDir, "decision-trace.png") });
  await assertPrivacy(page);
  await context.close();
}

async function recordStory(browser, filename, story) {
  const videoDir = path.join(runtimeDir, `${filename}-video`);
  await mkdir(videoDir, { recursive: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    recordVideo: { dir: videoDir, size: { width: 1280, height: 720 } },
  });
  const page = await context.newPage();
  await preparePage(page);
  const video = page.video();
  await story(page);
  await assertPrivacy(page);
  await page.close();
  await video.saveAs(path.join(runtimeDir, filename));
  await context.close();
}

async function captureDemo(browser) {
  await recordStory(browser, "demo.webm", async page => {
    await addTitleCard(page, "Crypto-Bot Research Platform", "Causal strategy research, validation and paper trading");
    await sleep(3200);
    await removeTitleCard(page);
    await sleep(6200);
    await openPrimary(page, "Market", "market");
    await page.locator("[data-market-state-page]").waitFor({ state: "visible" });
    await sleep(6200);
    await openOptimization(page);
    await sleep(8500);
    await openDecisionTrace(page);
    await sleep(7600);
    await openLifecycle(page);
    await sleep(6000);
    await addTitleCard(page, "Research only · No live exchange orders", "github.com/kevin6667890/crypto-bot");
    await sleep(3800);
  });
}

await mkdir(runtimeDir, { recursive: true });
const browser = await launchBrowser();
try {
  await captureScreenshots(browser);
  await captureDemo(browser);
  process.stdout.write("Playwright capture complete.\n");
} finally {
  await browser.close();
}
