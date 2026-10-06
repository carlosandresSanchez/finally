import { expect, test } from "@playwright/test";
import { cash, openApp } from "./helpers";

const DEFAULT = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"];

test.describe.configure({ mode: "serial" });

test("fresh start: default watchlist, $10k cash, streaming prices", async ({ page }) => {
  await openApp(page);
  for (const t of DEFAULT) {
    await expect(page.getByTestId(`watchlist-row-${t}`)).toBeVisible();
  }
  await expect(page.getByTestId("cash-balance")).toHaveText("$10,000.00");
  // Prices keep streaming: sparklines accumulate points.
  await expect(page.getByTestId("watchlist-row-TSLA").getByTestId("sparkline").locator("path")).toBeVisible();
});

test("add and remove a ticker from the watchlist", async ({ page }) => {
  await openApp(page);
  await page.getByTestId("watchlist-input").fill("PYPL");
  await page.getByTestId("watchlist-add").click();
  await expect(page.getByTestId("watchlist-row-PYPL")).toBeVisible();
  await expect(page.getByTestId("price-PYPL")).not.toHaveText("—");

  await page.getByTestId("watchlist-row-PYPL").hover();
  await page.getByTestId("watchlist-remove-PYPL").click();
  await expect(page.getByTestId("watchlist-row-PYPL")).toHaveCount(0);
});

test("buy shares: cash decreases and position appears", async ({ page }) => {
  await openApp(page);
  const before = await cash(page);
  await page.getByTestId("trade-ticker").fill("AAPL");
  await page.getByTestId("trade-quantity").fill("5");
  await page.getByTestId("trade-buy").click();
  await expect(page.getByTestId("trade-message")).toHaveText("Bought 5 AAPL");
  await expect(page.getByTestId("position-qty-AAPL")).toHaveText("5");
  expect(await cash(page)).toBeLessThan(before);
});

test("sell shares: cash increases and position updates/disappears", async ({ page }) => {
  await openApp(page);
  const before = await cash(page);
  await page.getByTestId("trade-ticker").fill("AAPL");
  await page.getByTestId("trade-quantity").fill("2");
  await page.getByTestId("trade-sell").click();
  await expect(page.getByTestId("position-qty-AAPL")).toHaveText("3");
  expect(await cash(page)).toBeGreaterThan(before);

  await page.getByTestId("trade-quantity").fill("3");
  await page.getByTestId("trade-sell").click();
  await expect(page.getByTestId("position-row-AAPL")).toHaveCount(0);
});

test("rejects selling more than owned", async ({ page }) => {
  await openApp(page);
  await page.getByTestId("trade-ticker").fill("NFLX");
  await page.getByTestId("trade-quantity").fill("1");
  await page.getByTestId("trade-sell").click();
  await expect(page.getByTestId("trade-message")).toContainText("Insufficient shares");
});

test("portfolio visualizations: heatmap cell and P&L chart data", async ({ page }) => {
  await openApp(page);
  await page.getByTestId("trade-ticker").fill("MSFT");
  await page.getByTestId("trade-quantity").fill("3");
  await page.getByTestId("trade-buy").click();
  const cell = page.getByTestId("heatmap-cell-MSFT");
  await expect(cell).toBeVisible();
  await expect(cell).toHaveAttribute("data-pnl", /profit|loss/);
  const points = Number(await page.getByTestId("pnl-chart").getAttribute("data-points"));
  expect(points).toBeGreaterThan(0);
});

test("AI chat (mocked): response with inline trade execution", async ({ page }) => {
  await openApp(page);
  await page.getByTestId("chat-input").fill("buy 2 NVDA");
  await page.getByTestId("chat-send").click();
  const trade = page.getByTestId("chat-trade").last();
  await expect(trade).toHaveAttribute("data-status", "executed");
  await expect(trade).toContainText("BUY 2 NVDA");
  await expect(page.getByTestId("position-qty-NVDA")).toHaveText("2");

  await page.getByTestId("chat-input").fill("add PYPL to my watchlist");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("chat-watchlist-change").last()).toContainText("PYPL added");
  await expect(page.getByTestId("watchlist-row-PYPL")).toBeVisible();
});

test("selecting a ticker updates the main chart", async ({ page }) => {
  await openApp(page);
  await page.getByTestId("watchlist-row-GOOGL").click();
  await expect(page.getByTestId("selected-ticker")).toHaveText("GOOGL");
  await expect
    .poll(async () => Number(await page.getByTestId("main-chart").getAttribute("data-points")))
    .toBeGreaterThan(1);
});

test("SSE resilience: shows reconnecting, then recovers", async ({ page }) => {
  await page.route("**/api/stream/prices", (route) => route.abort());
  await page.goto("/");
  await expect(page.getByTestId("connection-status")).not.toHaveAttribute("data-status", "connected");
  await page.unroute("**/api/stream/prices");
  // EventSource retries on its own (server sends retry: 1000).
  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "connected", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("price-AAPL")).not.toHaveText("—");
});
