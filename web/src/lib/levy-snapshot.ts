import {
  calibrationData,
  replayEvents,
  resolvedForecasts,
  tariffQuestions,
  type ReplayEvent,
  type TariffQuestion,
} from "@/lib/levy-data";

/** Desk-level counters surfaced by the agent service (`GET /api/stats`), folded into the snapshot. */
export interface LevyStats {
  itemsScanned: number;
  itemsTriaged: number;
  filteredByJev: number;
  pctFiltered: number;
  beliefsUpdated: number;
  activeQuestions: number;
  escalating: number;
  largestMove: number;
  largestMoveLabel: string;
}

/** Shape returned by the external LEVY agent service (backed by MongoDB Atlas). */
export interface LevySnapshot {
  source: "atlas" | "demo";
  questions: TariffQuestion[];
  replay: ReplayEvent[];
  calibration: { forecast: number; actual: number }[];
  resolved: { event: string; probability: number; outcome: string; score: number }[];
  brierScore: number;
  stats: LevyStats;
}

/** Derives desk metrics from a question set so demo + live snapshots share one code path. */
export function deriveStats(
  questions: TariffQuestion[],
  overrides: Partial<LevyStats> = {},
): LevyStats {
  const escalating = questions.filter((q) => q.status === "Escalating").length;
  const reviewed = questions.reduce((sum, q) => sum + (q.reviewed ?? 0), 0);
  const retained = questions.reduce((sum, q) => sum + (q.retained ?? 0), 0);
  const top = questions.reduce<TariffQuestion | null>(
    (best, q) => (best === null || Math.abs(q.change) > Math.abs(best.change) ? q : best),
    null,
  );
  return {
    itemsScanned: reviewed,
    itemsTriaged: reviewed,
    filteredByJev: Math.max(reviewed - retained, 0),
    pctFiltered: reviewed ? Math.round((1000 * (reviewed - retained)) / reviewed) / 10 : 0,
    beliefsUpdated: retained,
    activeQuestions: questions.length,
    escalating,
    largestMove: top?.change ?? 0,
    largestMoveLabel: top?.shortLabel ?? "—",
    ...overrides,
  };
}

/** Deterministic demo snapshot used whenever the live service is unset or unreachable. */
export function demoSnapshot(): LevySnapshot {
  return {
    source: "demo",
    questions: tariffQuestions,
    replay: replayEvents,
    calibration: calibrationData,
    resolved: resolvedForecasts,
    brierScore: 0.14,
    stats: deriveStats(tariffQuestions, {
      itemsScanned: 6235,
      itemsTriaged: 6235,
      beliefsUpdated: 127,
    }),
  };
}

/**
 * Validates and normalises an untrusted payload into a `LevySnapshot`, falling
 * back to demo fields for anything missing or malformed. Shared by the server
 * loader and the client live-refresh so both apply identical safety rules.
 * Returns `null` when the payload has no usable questions.
 */
export function normalizeSnapshot(
  data: Partial<LevySnapshot> | null | undefined,
  fallback: LevySnapshot = demoSnapshot(),
): LevySnapshot | null {
  if (!data || !Array.isArray(data.questions) || data.questions.length === 0) return null;
  const questions = data.questions;
  return {
    source: "atlas",
    questions,
    replay: Array.isArray(data.replay) ? data.replay : fallback.replay,
    calibration: Array.isArray(data.calibration) ? data.calibration : fallback.calibration,
    resolved: Array.isArray(data.resolved) ? data.resolved : fallback.resolved,
    brierScore: typeof data.brierScore === "number" ? data.brierScore : fallback.brierScore,
    stats:
      data.stats && typeof data.stats === "object"
        ? deriveStats(questions, data.stats)
        : deriveStats(questions),
  };
}
