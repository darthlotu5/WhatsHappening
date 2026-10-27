import { expect, test } from "@playwright/test";
import { eventDate } from "../utils/eventDate";

test("all configured sources expose explicit scrape outcomes", async ({ request }) => {
  const response = await request.get("/api/events/sources");
  expect(response.status()).toBe(200);
  const { sources } = await response.json();
  expect(sources).toHaveLength(19);
  expect(new Set(sources.map((source: { source: string }) => source.source)).size).toBe(19);
  const ticketsasa = sources.find((source: { source: string }) => source.source === "ticketsasa");
  expect(ticketsasa.status).toBe("healthy");
  expect(ticketsasa.event_count).toBeGreaterThan(0);
  expect(sources.every((source: { status: string }) =>
    ["healthy", "blocked", "unavailable", "incomplete", "rate_limited", "error"].includes(source.status),
  )).toBe(true);
});

test("unknown event dates are not rendered as epoch dates or guessed years", () => {
  expect(eventDate(null)).toBe("Date to be announced");
  expect(eventDate(null, "Wed, 11 Nov")).toBe("Wed, 11 Nov (year unconfirmed)");
  expect(eventDate("not-a-timestamp")).toBe("Date to be announced");
});

test("the deployed catalog contains real events from multiple new providers", async ({ request, page }) => {
  const response = await request.get("/api/events");
  expect(response.status()).toBe(200);
  const { events } = await response.json();
  const providers = new Set(events.flatMap((event: {
    site_name: string; sources: { site_name: string }[];
  }) => event.sources.map((source) => source.site_name)));
  expect(providers.size).toBeGreaterThanOrEqual(5);
  expect(providers.has("ticketsasa")).toBe(true);
  const partial = events.find((event: { date_status?: string; start_date: string | null; date_label?: string }) =>
    event.date_status === "unconfirmed" && event.start_date === null && !!event.date_label,
  );
  expect(partial).toBeTruthy();
  await page.goto("/events", { waitUntil: "domcontentloaded" });
  await expect(page.getByText(`${partial.date_label} (year unconfirmed)`, { exact: true }).first())
    .toBeVisible({ timeout: 30000 });
});
