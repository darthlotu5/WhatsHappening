import { clerkSetup } from "@clerk/testing/playwright";
import { test as setup } from "@playwright/test";

setup("configure Clerk development testing token", async () => {
  await clerkSetup();
});
