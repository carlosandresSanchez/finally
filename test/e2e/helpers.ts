import { expect, type Page } from "@playwright/test";

export const parseUsd = (s: string) => Number(s.replace(/[^0-9.-]/g, ""));

export async function cash(page: Page) {
  return parseUsd((await page.getByTestId("cash-balance").textContent()) ?? "");
}

export async function openApp(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "connected");
  await expect(page.getByTestId("price-AAPL")).not.toHaveText("—");
}
