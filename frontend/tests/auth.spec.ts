import { setupClerkTestingToken } from "@clerk/testing/playwright";
import { expect, test } from "@playwright/test";
import { randomBytes } from "node:crypto";

test("customer can sign up, access recommendations, and sign out", async ({ page }) => {
  const email = `dundaa-e2e-${Date.now()}+clerk_test@example.com`;
  const password = `${randomBytes(18).toString("base64url")}aA9!`;
  const key = process.env.CLERK_SECRET_KEY;
  if (!key?.startsWith("sk_test_")) throw new Error("Clerk development secret is required for test cleanup");
  await setupClerkTestingToken({ page });
  try {
    await page.goto("/register");
    await expect(page.locator(".cl-signUp-root")).toBeVisible({ timeout: 30000 });
    await page.locator('input[name="emailAddress"]').fill(email);
    await page.locator('input[name="password"]').fill(password);
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    const code = page.getByRole("textbox", { name: "Enter verification code" });
    await expect(code).toBeVisible({ timeout: 30000 });
    await code.pressSequentially("424242");
    await page.waitForURL(/\/$/, { timeout: 30000 });
    await expect(page.locator(".cl-userButtonTrigger")).toBeVisible({ timeout: 30000 });
    const response = await page.request.get("/api/events/recommended");
    expect(response.status()).toBe(200);
    expect(Array.isArray((await response.json()).events)).toBe(true);
    await page.locator(".cl-userButtonTrigger").click();
    await page.getByText("Sign out", { exact: true }).click();
    await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible({ timeout: 30000 });
    expect((await page.request.get("/api/events/recommended")).status()).toBe(401);
  } finally {
    const response = await fetch(`https://api.clerk.com/v1/users?email_address[]=${encodeURIComponent(email)}`, {
      headers: { Authorization: `Bearer ${key}` },
    });
    if (!response.ok) throw new Error(`Unable to find test users for cleanup: ${response.status}`);
    const users: { id: string }[] = await response.json();
    for (const user of users) {
      const deleted = await fetch(`https://api.clerk.com/v1/users/${user.id}`, {
        method: "DELETE", headers: { Authorization: `Bearer ${key}` },
      });
      if (!deleted.ok) throw new Error(`Test-user cleanup failed: ${deleted.status}`);
    }
  }
});
