import { createFileRoute } from "@tanstack/react-router";
import { createServerFn } from "@tanstack/react-start";

import { LevyDesk } from "@/components/levy/levy-desk";
import { loadLevySnapshot } from "@/lib/levy-source.server";

// Server function: guarantees the snapshot fetch (and any Authorization header /
// LEVY_API_KEY) runs ONLY on the server and is stripped from the client bundle.
const getLevySnapshot = createServerFn({ method: "GET" }).handler(() => loadLevySnapshot());

export const Route = createFileRoute("/")({
  // Server loader: reads the live snapshot (or deterministic demo data) once per
  // navigation. Secrets and the fetch never reach the client bundle.
  loader: () => getLevySnapshot(),
  head: () => ({
    meta: [
      { title: "LEVY — Tariff Intelligence Desk" },
      {
        name: "description",
        content:
          "Evidence-linked tariff forecasts, policy signals, and calibration in one decision workspace.",
      },
      { property: "og:title", content: "LEVY — Tariff Intelligence Desk" },
      {
        property: "og:description",
        content:
          "Evidence-linked tariff forecasts, policy signals, and calibration in one decision workspace.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Index,
});

function Index() {
  const snapshot = Route.useLoaderData();
  return <LevyDesk snapshot={snapshot} />;
}
