import { createOpenAI } from "@ai-sdk/openai";
import { convertToModelMessages, streamText, type UIMessage } from "ai";

import { loadLevySnapshot, type LevySnapshot } from "@/lib/levy-source.server";
import {
  createLovableAiGatewayRunIdFetch,
  getLovableAiGatewayRunId,
  withLovableAiGatewayRunIdHeader,
} from "./run-id.server";

const GATEWAY_URL = "https://ai.gateway.lovable.dev/v1";
const MODEL = "openai/gpt-6-astra";

function buildContext(snap: LevySnapshot) {
  return JSON.stringify({
    dataSource: snap.source === "atlas" ? "live agent service" : "demo data",
    questions: snap.questions.map((q) => ({
      id: q.id,
      market: q.market,
      question: q.question,
      probability: q.probability,
      change7d: q.change,
      confidence: q.confidence,
      horizon: q.horizon,
      status: q.status,
      signalsReviewed: q.reviewed,
      signalsRetained: q.retained,
      thesis: q.thesis,
      evidence: q.evidence,
      forecastTrail: q.forecast.map((p) => `${p.label}: ${p.probability}%`),
    })),
    canadaReplay: snap.replay,
    calibration: snap.calibration,
    brierScore: snap.brierScore,
    resolvedForecasts: snap.resolved,
  });
}

const SYSTEM = `You are LEVY's analyst assistant, a tariff-intelligence desk.
Users make requests (compare markets, explain a forecast move, summarize evidence, list highest risks, draft a briefing)
and pose WHAT-IF SCENARIOS (hypothetical events, e.g. "what if Canada raises steel tariffs to 60%?" or "suppose the EU delays its EV duties").

For normal requests, answer ONLY from the LEVY data below. Always:
- Lead with the direct finding in one sentence.
- Explain why, citing specific evidence items as (Source, date) and the probability numbers.
- Mention confidence grade and what could change the view.
- Use short markdown: bold key numbers, bullet lists, small tables when comparing.

For SCENARIOS, run a simulation:
- Open with "**Scenario simulation — hypothetical, not a live forecast.**"
- Identify which tracked questions the scenario touches (match by market, sector, or supply-chain links) and state each one's current baseline probability.
- Estimate a scenario-adjusted probability for each affected question, with a one-line rationale grounded in that question's evidence and thesis. Use a small table: Question | Baseline | Scenario | Shift.
- Explain second-order effects (retaliation risk, affected neighbors, signals LEVY would watch next).
- Close with what real evidence would confirm or kill the scenario.
Keep scenario adjustments plausible and proportional to the event size; never present them as real forecasts.

If the data does not cover a request, say so plainly and suggest what LEVY would need to monitor. Never invent sources.
Confidence grades and calibration are illustrative demo values; say so if asked about validation.

LEVY DATA:
`;

export async function handleLevyChat(request: Request) {
  const apiKey = process.env["LOVABLE_API_KEY"];
  if (!apiKey) return new Response("AI is not configured.", { status: 500 });

  let messages: UIMessage[];
  try {
    const body = (await request.json()) as { messages?: UIMessage[] };
    if (!Array.isArray(body.messages) || body.messages.length === 0) throw new Error();
    messages = body.messages.slice(-30);
  } catch {
    return new Response("Invalid request.", { status: 400 });
  }

  const runIdFetch = createLovableAiGatewayRunIdFetch(getLovableAiGatewayRunId(request));
  const provider = createOpenAI({
    baseURL: GATEWAY_URL,
    apiKey,
    headers: { "Lovable-API-Key": apiKey, "X-Lovable-AIG-SDK": "vercel-ai-sdk" },
    fetch: runIdFetch.fetch,
  });

  const result = streamText({
    model: provider.responses(MODEL),
    system: SYSTEM + buildContext(await loadLevySnapshot()),
    messages: await convertToModelMessages(messages),
    abortSignal: request.signal,
    providerOptions: {
      openai: {
        forceReasoning: true,
        reasoningEffort: "low",
        reasoningSummary: "auto",
        store: false,
        include: ["reasoning.encrypted_content"],
      },
    },
  });

  return withLovableAiGatewayRunIdHeader(
    result.toUIMessageStreamResponse({
      sendReasoning: true,
      onError: (error) => {
        const msg = error instanceof Error ? error.message : String(error);
        if (msg.includes("402"))
          return "AI credits are used up. Add credits in Settings → Plans & credits.";
        if (msg.includes("429"))
          return "Too many requests right now. Please wait a moment and try again.";
        if (msg.includes("403")) return "AI access is blocked for this workspace.";
        return "The assistant couldn't finish that answer.";
      },
    }),
    runIdFetch,
  );
}
