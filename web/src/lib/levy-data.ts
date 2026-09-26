export type QuestionStatus = "Escalating" | "Monitoring" | "Stable";
export type ConfidenceGrade = "A" | "B" | "C" | "D";

export interface ForecastPoint {
  label: string;
  probability: number;
  lower: number;
  upper: number;
}

export interface EvidenceItem {
  id: string;
  time: string;
  source: string;
  title: string;
  summary: string;
  reliability: "High" | "Medium";
  stance: "Raises" | "Lowers" | "Neutral";
  impact: number;
}

export interface TariffQuestion {
  id: string;
  market: string;
  flag: string;
  question: string;
  shortLabel: string;
  probability: number;
  change: number;
  confidence: ConfidenceGrade;
  horizon: string;
  status: QuestionStatus;
  reviewed: number;
  retained: number;
  thesis: string;
  forecast: ForecastPoint[];
  evidence: EvidenceItem[];
}

export interface ReplayEvent {
  date: string;
  kicker: string;
  headline: string;
  detail: string;
  probability: number;
  confidence: ConfidenceGrade;
  source: string;
  impact: number;
}

const canadaForecast: ForecastPoint[] = [
  { label: "Jun 03", probability: 34, lower: 21, upper: 48 },
  { label: "Jun 18", probability: 38, lower: 24, upper: 51 },
  { label: "Jul 02", probability: 46, lower: 31, upper: 59 },
  { label: "Jul 17", probability: 58, lower: 43, upper: 70 },
  { label: "Aug 01", probability: 71, lower: 58, upper: 81 },
  { label: "Aug 16", probability: 67, lower: 54, upper: 78 },
  { label: "Sep 01", probability: 74, lower: 63, upper: 83 },
];

export const tariffQuestions: TariffQuestion[] = [
  {
    id: "canada-steel",
    market: "Canada",
    flag: "CA",
    question: "Will Canada impose new steel counter-tariffs before October 1?",
    shortLabel: "Canada steel response",
    probability: 74,
    change: 12,
    confidence: "A",
    horizon: "5 days",
    status: "Escalating",
    reviewed: 1842,
    retained: 37,
    thesis:
      "Cabinet language, domestic producer pressure, and a completed consultation window now outweigh signals favoring another delay.",
    forecast: canadaForecast,
    evidence: [
      {
        id: "ca-1",
        time: "09:42",
        source: "Finance Canada",
        title: "Consultation period closes without extension",
        summary:
          "The ministry confirmed the review concluded on schedule and described countermeasures as ready for cabinet consideration.",
        reliability: "High",
        stance: "Raises",
        impact: 7,
      },
      {
        id: "ca-2",
        time: "08:18",
        source: "The Globe and Mail",
        title: "Cabinet weighs targeted steel response",
        summary:
          "Two officials describe a narrower package focused on products with domestic substitutes.",
        reliability: "High",
        stance: "Raises",
        impact: 4,
      },
      {
        id: "ca-3",
        time: "Yesterday",
        source: "PMO transcript",
        title: "Prime minister leaves negotiation channel open",
        summary: "Public remarks preserve room for a last-minute negotiated exemption.",
        reliability: "High",
        stance: "Lowers",
        impact: -3,
      },
    ],
  },
  {
    id: "eu-ev",
    market: "European Union",
    flag: "EU",
    question: "Will the EU raise duties on Chinese electric vehicles this quarter?",
    shortLabel: "EU electric vehicles",
    probability: 68,
    change: 5,
    confidence: "A",
    horizon: "31 days",
    status: "Monitoring",
    reviewed: 1254,
    retained: 29,
    thesis:
      "Member-state support remains sufficient, though negotiated price undertakings could narrow the final measure.",
    forecast: canadaForecast.map((point, index) => ({
      ...point,
      probability: [45, 49, 52, 57, 63, 65, 68][index] ?? point.probability,
    })),
    evidence: [
      {
        id: "eu-1",
        time: "10:05",
        source: "European Commission",
        title: "Technical talks continue",
        summary:
          "The Commission reported progress but no acceptable undertaking covering the full injury finding.",
        reliability: "High",
        stance: "Raises",
        impact: 3,
      },
      {
        id: "eu-2",
        time: "Yesterday",
        source: "Handelsblatt",
        title: "Germany presses for settlement",
        summary:
          "Industry-linked officials continue to lobby for a negotiated alternative to higher duties.",
        reliability: "Medium",
        stance: "Lowers",
        impact: -2,
      },
    ],
  },
  {
    id: "mexico-auto",
    market: "Mexico",
    flag: "MX",
    question: "Will Mexico announce new auto-parts tariffs before year-end?",
    shortLabel: "Mexico auto parts",
    probability: 61,
    change: 9,
    confidence: "B",
    horizon: "96 days",
    status: "Escalating",
    reviewed: 906,
    retained: 18,
    thesis:
      "The industrial policy case is strengthening, but timing remains exposed to the regional review calendar.",
    forecast: canadaForecast.map((point, index) => ({
      ...point,
      probability: [29, 33, 35, 42, 50, 52, 61][index] ?? point.probability,
    })),
    evidence: [
      {
        id: "mx-1",
        time: "07:36",
        source: "Economía",
        title: "Import substitution list expanded",
        summary:
          "Auto electronics and drivetrain components appeared in the ministry's updated strategic categories.",
        reliability: "High",
        stance: "Raises",
        impact: 6,
      },
      {
        id: "mx-2",
        time: "Sep 24",
        source: "Reforma",
        title: "Industry requests phased implementation",
        summary: "Manufacturers warn immediate measures could disrupt assembly schedules.",
        reliability: "Medium",
        stance: "Lowers",
        impact: -2,
      },
    ],
  },
  {
    id: "india-solar",
    market: "India",
    flag: "IN",
    question: "Will India extend solar-module safeguards into 2027?",
    shortLabel: "India solar safeguards",
    probability: 57,
    change: -4,
    confidence: "B",
    horizon: "64 days",
    status: "Monitoring",
    reviewed: 633,
    retained: 14,
    thesis:
      "Domestic capacity arguments remain persuasive, but project-cost concerns are gaining ministerial attention.",
    forecast: canadaForecast.map((point, index) => ({
      ...point,
      probability: [66, 64, 63, 62, 60, 59, 57][index] ?? point.probability,
    })),
    evidence: [
      {
        id: "in-1",
        time: "Sep 25",
        source: "MNRE circular",
        title: "Developer comment window reopened",
        summary:
          "The ministry requested additional evidence on project delays and module availability.",
        reliability: "High",
        stance: "Lowers",
        impact: -4,
      },
    ],
  },
  {
    id: "us-chips",
    market: "United States",
    flag: "US",
    question: "Will the U.S. broaden semiconductor import controls this quarter?",
    shortLabel: "U.S. chip controls",
    probability: 43,
    change: 1,
    confidence: "B",
    horizon: "42 days",
    status: "Stable",
    reviewed: 1118,
    retained: 21,
    thesis:
      "Agency preparation is visible, but allied coordination appears incomplete and limits near-term action.",
    forecast: canadaForecast.map((point, index) => ({
      ...point,
      probability: [39, 41, 45, 44, 42, 42, 43][index] ?? point.probability,
    })),
    evidence: [
      {
        id: "us-1",
        time: "Sep 24",
        source: "Federal Register",
        title: "No new rule enters review",
        summary: "The latest agenda update contains no expanded product-control package.",
        reliability: "High",
        stance: "Lowers",
        impact: -2,
      },
    ],
  },
  {
    id: "brazil-chemicals",
    market: "Brazil",
    flag: "BR",
    question: "Will Brazil renew temporary chemical import duties?",
    shortLabel: "Brazil chemicals",
    probability: 36,
    change: -7,
    confidence: "C",
    horizon: "18 days",
    status: "Stable",
    reviewed: 482,
    retained: 9,
    thesis:
      "The renewal case weakened after inflation concerns entered the inter-ministerial review.",
    forecast: canadaForecast.map((point, index) => ({
      ...point,
      probability: [51, 49, 47, 44, 42, 39, 36][index] ?? point.probability,
    })),
    evidence: [
      {
        id: "br-1",
        time: "Sep 23",
        source: "Valor Econômico",
        title: "Finance ministry resists extension",
        summary: "Officials cited downstream price pressure in an internal impact review.",
        reliability: "Medium",
        stance: "Lowers",
        impact: -5,
      },
    ],
  },
];

export const replayEvents: ReplayEvent[] = [
  {
    date: "JUN 03",
    kicker: "Baseline",
    headline: "Review opens quietly",
    detail:
      "Ottawa opens a 30-day consultation. Trade retaliation is possible, but negotiations remain the base case.",
    probability: 34,
    confidence: "C",
    source: "Finance Canada",
    impact: 0,
  },
  {
    date: "JUN 18",
    kicker: "Signal 01",
    headline: "Producer coalition hardens position",
    detail:
      "Canadian steelmakers publish a joint demand for reciprocal tariffs and identify substitute product lines.",
    probability: 41,
    confidence: "C",
    source: "CSPA release",
    impact: 7,
  },
  {
    date: "JUL 02",
    kicker: "Signal 02",
    headline: "Consultation closes on schedule",
    detail:
      "No extension is announced. The policy window advances from exploratory review to cabinet preparation.",
    probability: 49,
    confidence: "B",
    source: "Finance Canada",
    impact: 8,
  },
  {
    date: "JUL 17",
    kicker: "Signal 03",
    headline: "Draft list circulates",
    detail:
      "Two independent reports converge on a targeted product list designed to limit domestic price effects.",
    probability: 61,
    confidence: "B",
    source: "Reuters + Globe",
    impact: 12,
  },
  {
    date: "AUG 01",
    kicker: "Signal 04",
    headline: "Cabinet language shifts",
    detail:
      "The prime minister describes countermeasures as necessary rather than hypothetical for the first time.",
    probability: 71,
    confidence: "A",
    source: "PMO transcript",
    impact: 10,
  },
  {
    date: "AUG 16",
    kicker: "Counter-signal",
    headline: "Negotiations reopen",
    detail:
      "A bilateral working session creates a narrow path to exemption, trimming the forecast without reversing it.",
    probability: 67,
    confidence: "A",
    source: "USTR readout",
    impact: -4,
  },
  {
    date: "SEP 01",
    kicker: "Current view",
    headline: "Implementation package is ready",
    detail:
      "Reporting and official language now indicate the package can be activated within days if talks fail.",
    probability: 74,
    confidence: "A",
    source: "Finance Canada",
    impact: 7,
  },
];

export const calibrationData = [
  { forecast: 10, actual: 8 },
  { forecast: 25, actual: 29 },
  { forecast: 40, actual: 38 },
  { forecast: 55, actual: 58 },
  { forecast: 70, actual: 66 },
  { forecast: 85, actual: 88 },
];

export const resolvedForecasts = [
  { event: "EU steel safeguard renewal", probability: 82, outcome: "Occurred", score: 0.03 },
  { event: "Turkey grain duty increase", probability: 64, outcome: "Occurred", score: 0.13 },
  { event: "Japan battery subsidy review", probability: 71, outcome: "Did not occur", score: 0.5 },
  { event: "UK carbon border delay", probability: 38, outcome: "Occurred", score: 0.38 },
];
