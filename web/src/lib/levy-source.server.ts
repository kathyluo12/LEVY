import { demoSnapshot, normalizeSnapshot, type LevySnapshot } from "@/lib/levy-snapshot";

export type { LevySnapshot, LevyStats } from "@/lib/levy-snapshot";

/**
 * Reads the live snapshot from the separate agent service (GET {LEVY_API_URL}/v1/snapshot).
 *
 * Server-only (this module is `.server.ts`, so the fetch and any header logic are
 * never shipped to the client bundle).
 *
 * - `LEVY_API_URL` alone is enough to go live.
 * - `LEVY_API_KEY` is optional; the `Authorization` header is only sent when it is set.
 * - Any unset / unreachable / invalid response falls back to deterministic demo data so the
 *   demo never breaks. MongoDB and model credentials never touch this layer.
 */
export async function loadLevySnapshot(): Promise<LevySnapshot> {
  const base = process.env["LEVY_API_URL"];
  const key = process.env["LEVY_API_KEY"];
  if (!base) return demoSnapshot();

  try {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (key) headers["Authorization"] = `Bearer ${key}`;

    const res = await fetch(`${base.replace(/\/+$/, "")}/v1/snapshot`, {
      headers,
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) {
      console.error(`LEVY API failed [${res.status}]: ${await res.text()}`);
      return demoSnapshot();
    }

    const data = (await res.json()) as Partial<LevySnapshot>;
    return normalizeSnapshot(data) ?? demoSnapshot();
  } catch (error) {
    console.error("LEVY API unreachable:", error);
    return demoSnapshot();
  }
}
