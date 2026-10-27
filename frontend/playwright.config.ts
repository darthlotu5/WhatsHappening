import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

if (existsSync(".env.local")) process.loadEnvFile(".env.local");
process.env.CLERK_PUBLISHABLE_KEY = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

export default defineConfig({
  testDir: "./tests",
  timeout: 90000,
  projects: [
    { name: "setup", testMatch: /global\.setup\.ts/ },
    { name: "chromium", testMatch: /.*\.spec\.ts/, dependencies: ["setup"] },
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:3000",
    browserName: "chromium",
    trace: "retain-on-failure",
  },
  reporter: "list",
});
