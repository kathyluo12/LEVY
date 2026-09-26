import { useEffect, useRef, useState } from "react";

import { normalizeSnapshot, type LevySnapshot } from "@/lib/levy-snapshot";

/** Stream event names emitted by the agent service that should trigger a refetch. */
const REFRESH_EVENTS = ["belief", "evidence", "job", "lesson", "resolution"] as const;
const DEBOUNCE_MS = 800;

/**
 * Client-side live refresh.
 *
 * Starts from the server/loader snapshot and, when a public `VITE_LEVY_API_URL`
 * is configured, subscribes to `{base}/api/stream` (SSE). On belief/evidence/
 * job/lesson/resolution events it debounces then refetches `{base}/v1/snapshot`.
 *
 * - Uses ONLY the public Vite URL — never a key. No `Authorization` is sent from
 *   the browser, and no credentials of any kind leak into client code.
 * - Closes the EventSource on cleanup.
 * - Retains the last good (initial/server) snapshot if a fetch or the stream fails.
 * - With no `VITE_LEVY_API_URL`, it is a no-op and the loader data stands.
 */
export function useLiveSnapshot(initial: LevySnapshot): LevySnapshot {
  const [snapshot, setSnapshot] = useState<LevySnapshot>(initial);
  const latest = useRef<LevySnapshot>(initial);

  // Keep the ref in sync so error paths can retain the current snapshot, and
  // adopt fresh server data if the loader re-runs (navigation / invalidation).
  useEffect(() => {
    latest.current = initial;
    setSnapshot(initial);
  }, [initial]);

  useEffect(() => {
    const raw = import.meta.env["VITE_LEVY_API_URL"] as string | undefined;
    const base = raw?.replace(/\/+$/, "");
    if (!base) return; // loader data still works with no Vite URL

    let closed = false;
    let debounce: ReturnType<typeof setTimeout> | undefined;

    const refetch = async () => {
      try {
        const res = await fetch(`${base}/v1/snapshot`, {
          headers: { Accept: "application/json" },
        });
        if (!res.ok) return; // keep prior snapshot on non-2xx
        const data = (await res.json()) as Partial<LevySnapshot>;
        const next = normalizeSnapshot(data, latest.current);
        if (!next || closed) return; // keep prior snapshot on empty/invalid
        latest.current = next;
        setSnapshot(next);
      } catch {
        // Network / parse error: retain the last good snapshot.
      }
    };

    const scheduleRefetch = () => {
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(refetch, DEBOUNCE_MS);
    };

    let source: EventSource | undefined;
    try {
      source = new EventSource(`${base}/api/stream`);
      for (const name of REFRESH_EVENTS) {
        source.addEventListener(name, scheduleRefetch);
      }
      // Some SSE servers emit unnamed `message` events; refresh on those too.
      source.addEventListener("message", scheduleRefetch);
    } catch {
      // EventSource unavailable/blocked: stay on the server snapshot.
    }

    return () => {
      closed = true;
      if (debounce) clearTimeout(debounce);
      if (source) {
        for (const name of REFRESH_EVENTS) {
          source.removeEventListener(name, scheduleRefetch);
        }
        source.removeEventListener("message", scheduleRefetch);
        source.close();
      }
    };
  }, []);

  return snapshot;
}
