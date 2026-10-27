import { getAuth } from "@clerk/nextjs/server";
import type { NextApiRequest, NextApiResponse } from "next";

export default async function events(req: NextApiRequest, res: NextApiResponse) {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ error: "Method not allowed" });
  }
  const path = req.query.path ?? [];
  if (!Array.isArray(path) || path.length > 1 || (path.length === 1 && !["recommended", "sources"].includes(path[0]))) {
    return res.status(404).json({ error: "Not found" });
  }
  let endpoint = "/events/";
  if (path[0] === "sources") endpoint = "/sources";
  if (path[0] === "recommended") {
    const { userId } = getAuth(req);
    if (!userId) return res.status(401).json({ error: "Sign in to view recommended events" });
    endpoint = `/events/recommended/${encodeURIComponent(userId)}`;
  }
  const baseUrl = process.env.EVENTS_API_URL;
  if (!baseUrl) {
    console.error("EVENTS_API_URL is required");
    return res.status(503).json({ error: "Events service is not configured" });
  }
  try {
    const response = await fetch(`${baseUrl.replace(/\/$/, "")}${endpoint}`, {
      signal: AbortSignal.timeout(30000),
      redirect: "error",
    });
    if (!response.ok) throw new Error(`Events service returned HTTP ${response.status}`);
    const data: unknown = await response.json();
    if (typeof data !== "object" || data === null) {
      throw new Error("Events service returned an invalid response");
    }
    const records = path[0] === "sources"
      ? ("sources" in data ? data.sources : undefined)
      : ("events" in data ? data.events : undefined);
    if (!Array.isArray(records)) {
      throw new Error("Events service returned an invalid response");
    }
    return res.status(200).json(data);
  } catch (error) {
    console.error("Events request failed", error);
    return res.status(502).json({ error: "Unable to load events" });
  }
}
