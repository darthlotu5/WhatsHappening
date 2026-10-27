import { expect, test } from "@playwright/test";
import { setupClerkTestingToken } from "@clerk/testing/playwright";

test.beforeEach(async ({ page }) => {
  await setupClerkTestingToken({ page });
});

test("homepage reaches the persistent event backend", async ({ page, request }) => {
  const response = await request.get("/api/events");
  expect(response.status()).toBe(200);
  const data = await response.json();
  expect(Array.isArray(data.events)).toBe(true);
  expect(data.events.length).toBeGreaterThan(0);
  await page.goto("/");
  await expect(page.getByText("Upcoming Events", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await expect(page.getByText("An error occurred. Please try again!")).toHaveCount(0);
});

test("all events page renders persisted pipeline data", async ({ page, request }) => {
  const response = await request.get("/api/events");
  expect(response.status()).toBe(200);
  const data = await response.json();
  expect(data.events.length).toBeGreaterThan(0);
  const eventName = process.env.E2E_EVENT_NAME || data.events[0].name;
  expect(typeof eventName).toBe("string");
  await page.goto("/events");
  await expect(page.getByText(eventName, { exact: true })).toBeVisible();
});

test("anonymous users cannot request recommendations", async ({ request }) => {
  const response = await request.get("/api/events/recommended");
  expect(response.status()).toBe(401);
});

test("event proxy rejects unsupported methods and paths", async ({ request }) => {
  expect((await request.post("/api/events")).status()).toBe(405);
  expect((await request.get("/api/events/arbitrary")).status()).toBe(404);
});

test("Clerk sign-in and signup widgets load", async ({ page }) => {
  await page.goto("/login");
  await expect(page.locator(".cl-signIn-root")).toBeVisible({ timeout: 30000 });
  await page.goto("/register");
  await expect(page.locator(".cl-signUp-root")).toBeVisible({ timeout: 30000 });
});
