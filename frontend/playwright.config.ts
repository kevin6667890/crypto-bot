import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e", timeout: 30_000, fullyParallel: false, workers: 1,
  reporter: [["list"]], use: { baseURL: "http://127.0.0.1:4173", trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: { command: "npm run dev -- --port 4173", url: "http://127.0.0.1:4173", reuseExistingServer: false,
    env: { ...process.env, VITE_AI_MARKET_ANALYSIS_SHADOW_ENABLED: "true" } },
  projects: [{ name: "chromium", use: { browserName: "chromium", launchOptions: { executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe" } } }],
});
