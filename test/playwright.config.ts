import { defineConfig, devices } from "@playwright/test";

// BASE_URL points at a running FinAlly (LLM_MOCK=true, fresh database).
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1, // tests share one single-user backend
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:8000",
    viewport: { width: 1600, height: 1000 },
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } } }],
});
