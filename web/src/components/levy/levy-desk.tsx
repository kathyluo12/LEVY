import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  BookOpen,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  Gauge,
  Globe2,
  Pause,
  Play,
  Radar,
  RefreshCcw,
  Search,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { LevyChat } from "@/components/levy/levy-chat";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useLiveSnapshot } from "@/hooks/use-live-snapshot";
import { cn } from "@/lib/utils";
import { type QuestionStatus, type ReplayEvent, type TariffQuestion } from "@/lib/levy-data";
import type { LevySnapshot } from "@/lib/levy-snapshot";

const statusOptions: Array<"All" | QuestionStatus> = ["All", "Escalating", "Monitoring", "Stable"];

function Delta({ value }: { value: number }) {
  const rising = value > 0;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 font-mono text-xs font-semibold",
        rising ? "text-risk" : "text-positive",
      )}
    >
      {rising ? <ArrowUpRight /> : <ArrowDownRight />}
      {Math.abs(value)} pts
    </span>
  );
}

function Confidence({ grade }: { grade: TariffQuestion["confidence"] }) {
  return (
    <span
      className={cn(
        "inline-grid size-7 place-items-center rounded-sm border font-mono text-xs font-bold",
        grade === "A"
          ? "border-positive/30 bg-positive-soft text-positive"
          : grade === "B"
            ? "border-signal/30 bg-signal-soft text-signal"
            : grade === "C"
              ? "border-warning/30 bg-warning-soft text-warning"
              : "border-risk/30 bg-risk-soft text-risk",
      )}
    >
      {grade}
    </span>
  );
}

function Metric({
  label,
  value,
  detail,
  icon: Icon,
  delay,
}: {
  label: string;
  value: string;
  detail: string;
  icon: typeof Activity;
  delay: string;
}) {
  return (
    <div
      className="metric-enter border-r border-border px-5 py-4 last:border-r-0"
      style={{ animationDelay: delay }}
    >
      <div className="mb-3 flex items-center justify-between text-muted-foreground">
        <span className="label-caps">{label}</span>
        <Icon className="size-4" />
      </div>
      <div className="font-display text-3xl font-semibold tracking-tight text-foreground">
        {value}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
    </div>
  );
}

function EmptyPanel({ label }: { label: string }) {
  return (
    <div className="border border-dashed border-border bg-surface px-6 py-12 text-center">
      <p className="text-sm font-medium text-muted-foreground">{label}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Data will appear once the agent service reports it.
      </p>
    </div>
  );
}

function Watchlist({
  questions,
  selectedId,
  onSelect,
}: {
  questions: TariffQuestion[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<(typeof statusOptions)[number]>("All");
  const filtered = questions.filter(
    (item) =>
      item.question.toLowerCase().includes(query.toLowerCase()) &&
      (status === "All" || item.status === status),
  );

  return (
    <section className="border border-border bg-surface">
      <div className="flex flex-col gap-3 border-b border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <span className="label-caps text-muted-foreground">Forecast portfolio</span>
          <h2 className="mt-1 font-display text-lg font-semibold">Active questions</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex h-8 min-w-48 items-center gap-2 border border-border bg-background px-2.5 text-xs text-muted-foreground focus-within:border-ring">
            <Search className="size-3.5" />
            <input
              aria-label="Search questions"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search questions"
              className="w-full bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
            />
          </label>
          <div
            className="flex border border-border bg-background p-0.5"
            aria-label="Filter questions by status"
          >
            {statusOptions.map((option) => (
              <button
                key={option}
                onClick={() => setStatus(option)}
                className={cn(
                  "h-7 px-2 text-[10px] font-semibold uppercase text-muted-foreground transition-colors",
                  status === option && "bg-foreground text-background",
                )}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
      </div>
      {filtered.length === 0 ? (
        <EmptyPanel
          label={questions.length === 0 ? "No active questions" : "No questions match this filter"}
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] table-fixed text-left">
            <thead className="border-b border-border bg-muted/50 text-[10px] font-semibold uppercase text-muted-foreground">
              <tr>
                <th className="w-[47%] px-4 py-2.5">Question</th>
                <th className="px-3">Probability</th>
                <th className="px-3">Move</th>
                <th className="px-3">Grade</th>
                <th className="px-3">Horizon</th>
                <th className="w-9" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => (
                <tr
                  key={item.id}
                  onClick={() => onSelect(item.id)}
                  className={cn(
                    "group cursor-pointer border-b border-border/70 transition-colors last:border-b-0 hover:bg-accent/50",
                    selectedId === item.id && "bg-selected",
                  )}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-start gap-3">
                      <span className="mt-0.5 inline-grid h-6 min-w-7 place-items-center border border-border bg-background px-1 font-mono text-[9px] font-bold">
                        {item.flag}
                      </span>
                      <div>
                        <p className="line-clamp-1 text-sm font-medium">{item.question}</p>
                        <p className="mt-1 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                          <span
                            className={cn(
                              "size-1.5 rounded-full",
                              item.status === "Escalating"
                                ? "bg-risk"
                                : item.status === "Monitoring"
                                  ? "bg-warning"
                                  : "bg-positive",
                            )}
                          />
                          {item.status}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td className="px-3">
                    <span className="font-display text-xl font-semibold">{item.probability}%</span>
                  </td>
                  <td className="px-3">
                    <Delta value={item.change} />
                  </td>
                  <td className="px-3">
                    <Confidence grade={item.confidence} />
                  </td>
                  <td className="px-3 font-mono text-xs text-muted-foreground">{item.horizon}</td>
                  <td>
                    <ChevronRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-foreground" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ForecastChart({ question }: { question: TariffQuestion }) {
  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={question.forecast} margin={{ top: 12, right: 10, bottom: 0, left: -24 }}>
          <defs>
            <linearGradient id="probabilityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--signal)" stopOpacity={0.2} />
              <stop offset="100%" stopColor="var(--signal)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="2 4" />
          <XAxis
            dataKey="label"
            axisLine={false}
            tickLine={false}
            tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
          />
          <YAxis
            domain={[0, 100]}
            axisLine={false}
            tickLine={false}
            tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
          />
          <ChartTooltip
            contentStyle={{
              background: "var(--popover)",
              border: "1px solid var(--border)",
              borderRadius: 2,
              fontSize: 12,
            }}
            formatter={(value) => [`${value}%`, "Probability"]}
          />
          <Area
            type="monotone"
            dataKey="probability"
            stroke="var(--signal)"
            strokeWidth={2.5}
            fill="url(#probabilityFill)"
            animationDuration={900}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function QuestionDetail({ question }: { question: TariffQuestion }) {
  const discarded = Math.max(question.reviewed - question.retained, 0);
  const lastPoint = question.forecast.at(-1);
  return (
    <section
      key={question.id}
      className="panel-enter grid border border-border bg-surface xl:grid-cols-[1.35fr_0.85fr]"
    >
      <div className="border-b border-border p-5 xl:border-r xl:border-b-0">
        <div className="flex items-start justify-between gap-6">
          <div>
            <span className="label-caps text-signal">Selected question / {question.market}</span>
            <h2 className="mt-2 max-w-2xl font-display text-xl font-semibold leading-tight sm:text-2xl">
              {question.question}
            </h2>
          </div>
          <div className="shrink-0 text-right">
            <div className="font-display text-4xl font-semibold text-signal">
              {question.probability}%
            </div>
            <Delta value={question.change} />
          </div>
        </div>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">{question.thesis}</p>
        <div className="mt-4 flex items-center justify-between border-y border-border py-2 text-xs">
          <span className="flex items-center gap-2 text-muted-foreground">
            <CircleDot className="size-3.5 text-signal" /> Base forecast
          </span>
          <span className="font-mono text-muted-foreground">
            {lastPoint ? `Range ${lastPoint.lower}–${lastPoint.upper}%` : "Range —"}
          </span>
        </div>
        {question.forecast.length > 0 ? (
          <ForecastChart question={question} />
        ) : (
          <EmptyPanel label="No forecast history yet" />
        )}
        <div className="mt-3 grid grid-cols-3 border border-border bg-background">
          <div className="p-3">
            <span className="label-caps text-muted-foreground">Reviewed</span>
            <p className="mt-1 font-mono text-lg font-semibold">
              {question.reviewed.toLocaleString()}
            </p>
          </div>
          <div className="border-x border-border p-3">
            <span className="label-caps text-muted-foreground">Retained</span>
            <p className="mt-1 font-mono text-lg font-semibold text-positive">
              {question.retained}
            </p>
          </div>
          <div className="p-3">
            <span className="label-caps text-muted-foreground">Filtered</span>
            <p className="mt-1 font-mono text-lg font-semibold text-muted-foreground">
              {discarded.toLocaleString()}
            </p>
          </div>
        </div>
        <p className="mt-2 text-[10px] text-muted-foreground">
          Jev evidence filter · duplicates, unsupported claims, and low-reliability items removed
        </p>
      </div>
      <div className="min-w-0">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <span className="label-caps text-muted-foreground">Belief timeline</span>
            <h3 className="mt-1 text-sm font-semibold">Why the forecast moved</h3>
          </div>
          <Radar className="size-4 text-signal" />
        </div>
        {question.evidence.length === 0 ? (
          <EmptyPanel label="No evidence recorded yet" />
        ) : (
          <div className="divide-y divide-border">
            {question.evidence.map((item, index) => (
              <article
                key={item.id}
                className="relative px-4 py-4 pl-9 transition-colors hover:bg-accent/40"
              >
                <span className="absolute left-4 top-5 grid size-3 place-items-center rounded-full border border-signal bg-surface">
                  <span className="size-1 rounded-full bg-signal" />
                </span>
                {index < question.evidence.length - 1 && (
                  <span className="absolute bottom-0 left-[21px] top-8 w-px bg-border" />
                )}
                <div className="flex items-center justify-between gap-3 text-[10px] text-muted-foreground">
                  <span className="font-mono">
                    {item.time} · {item.source}
                  </span>
                  <span
                    className={cn(
                      "font-mono font-semibold",
                      item.impact > 0 ? "text-risk" : "text-positive",
                    )}
                  >
                    {item.impact > 0 ? "+" : ""}
                    {item.impact} pts
                  </span>
                </div>
                <h4 className="mt-1.5 text-sm font-semibold leading-snug">{item.title}</h4>
                <p className="mt-1.5 text-xs leading-5 text-muted-foreground">{item.summary}</p>
                <div className="mt-2 flex gap-2">
                  <span className="tag">{item.reliability} reliability</span>
                  <span className="tag">{item.stance} risk</span>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function CommandView({ snapshot }: { snapshot: LevySnapshot }) {
  const { questions, stats } = snapshot;
  const [selectedId, setSelectedId] = useState(questions[0]?.id ?? "");
  const selected = questions.find((item) => item.id === selectedId) ?? questions[0];

  const metrics = useMemo(() => {
    const activeQuestions = stats.activeQuestions || questions.length;
    const escalating =
      stats.escalating || questions.filter((q) => q.status === "Escalating").length;
    const scanned = stats.itemsScanned;
    const retained = stats.beliefsUpdated;
    const largest = stats.largestMove;
    return {
      active: String(activeQuestions).padStart(2, "0"),
      activeDetail: `${escalating} escalating now`,
      scanned: scanned.toLocaleString(),
      scannedDetail: `${retained.toLocaleString()} retained`,
      largest: `${largest > 0 ? "+" : ""}${largest} pts`,
      largestDetail: stats.largestMoveLabel,
      brier: snapshot.brierScore.toFixed(2),
    };
  }, [questions, stats, snapshot.brierScore]);

  return (
    <div className="space-y-4">
      <div className="grid border border-border bg-surface sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="Active questions"
          value={metrics.active}
          detail={metrics.activeDetail}
          icon={Radar}
          delay="0ms"
        />
        <Metric
          label="Evidence processed"
          value={metrics.scanned}
          detail={metrics.scannedDetail}
          icon={Zap}
          delay="70ms"
        />
        <Metric
          label="Largest movement"
          value={metrics.largest}
          detail={metrics.largestDetail}
          icon={Activity}
          delay="140ms"
        />
        <Metric
          label="Calibration"
          value={metrics.brier}
          detail="Brier score · last 30"
          icon={Gauge}
          delay="210ms"
        />
      </div>
      {questions.length === 0 || !selected ? (
        <EmptyPanel label="No active questions" />
      ) : (
        <>
          <Watchlist questions={questions} selectedId={selected.id} onSelect={setSelectedId} />
          <QuestionDetail question={selected} />
        </>
      )}
    </div>
  );
}

function ReplayView({ replay }: { replay: ReplayEvent[] }) {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const safeStep = replay.length > 0 ? Math.min(step, replay.length - 1) : 0;
  const current = replay[safeStep];
  useEffect(() => {
    if (!playing || replay.length === 0) return;
    const timer = window.setInterval(
      () =>
        setStep((value) => {
          if (value >= replay.length - 1) {
            setPlaying(false);
            return value;
          }
          return value + 1;
        }),
      1800,
    );
    return () => window.clearInterval(timer);
  }, [playing, replay.length]);

  if (replay.length === 0 || !current) {
    return <EmptyPanel label="No replay history available" />;
  }

  const chartData = replay
    .slice(0, safeStep + 1)
    .map((event) => ({ date: event.date, probability: event.probability }));
  return (
    <div className="grid gap-4 xl:grid-cols-[1.4fr_0.8fr]">
      <section className="border border-border bg-surface p-5">
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
          <div>
            <span className="label-caps text-signal">Case replay / Canada</span>
            <h2 className="mt-2 font-display text-2xl font-semibold">How conviction formed</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              A deterministic evidence-to-forecast reconstruction.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="icon"
              aria-label="Restart replay"
              onClick={() => {
                setStep(0);
                setPlaying(false);
              }}
            >
              <RefreshCcw />
            </Button>
            <Button
              aria-label={playing ? "Pause replay" : "Play replay"}
              onClick={() => setPlaying(!playing)}
            >
              {playing ? <Pause /> : <Play />} {playing ? "Pause" : "Play replay"}
            </Button>
          </div>
        </div>
        <div className="mt-8 flex items-end justify-between border-b border-border pb-4">
          <div>
            <span className="label-caps text-muted-foreground">Forecast at {current.date}</span>
            <div className="mt-1 font-display text-6xl font-semibold text-signal">
              {current.probability}%
            </div>
          </div>
          <div className="text-right">
            <span className="label-caps text-muted-foreground">Confidence</span>
            <div className="mt-2 flex justify-end">
              <Confidence grade={current.confidence} />
            </div>
          </div>
        </div>
        <div className="mt-5 h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 15, right: 15, bottom: 0, left: -20 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="2 4" />
              <XAxis
                dataKey="date"
                axisLine={false}
                tickLine={false}
                tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
              />
              <YAxis
                domain={[20, 90]}
                axisLine={false}
                tickLine={false}
                tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
              />
              <Line
                type="monotone"
                dataKey="probability"
                stroke="var(--signal)"
                strokeWidth={3}
                dot={{ fill: "var(--signal)", r: 4, strokeWidth: 0 }}
                animationDuration={500}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-5">
          <Slider
            aria-label="Replay progress"
            value={[safeStep]}
            min={0}
            max={replay.length - 1}
            step={1}
            onValueChange={(value) => {
              setStep(value[0] ?? 0);
              setPlaying(false);
            }}
          />
          <div className="mt-3 flex justify-between">
            {replay.map((event, index) => (
              <button
                key={`${event.date}-${index}`}
                onClick={() => {
                  setStep(index);
                  setPlaying(false);
                }}
                className={cn(
                  "font-mono text-[9px] text-muted-foreground",
                  index <= safeStep && "text-foreground",
                )}
              >
                {event.date}
              </button>
            ))}
          </div>
        </div>
      </section>
      <section key={safeStep} className="panel-enter border border-border bg-ink p-6 text-paper">
        <div className="flex items-center justify-between">
          <span className="label-caps text-paper-muted">{current.kicker}</span>
          <span
            className={cn(
              "font-mono text-sm font-bold",
              current.impact < 0 ? "text-positive-bright" : "text-risk-bright",
            )}
          >
            {current.impact > 0 ? "+" : ""}
            {current.impact} pts
          </span>
        </div>
        <div className="my-10 grid size-14 place-items-center border border-paper-dim">
          <Sparkles className="size-5 text-signal-bright" />
        </div>
        <h3 className="font-display text-3xl font-semibold leading-tight">{current.headline}</h3>
        <p className="mt-4 text-sm leading-7 text-paper-muted">{current.detail}</p>
        <div className="mt-8 border-t border-paper-dim pt-4">
          <span className="label-caps text-paper-muted">Primary source</span>
          <p className="mt-2 flex items-center gap-2 text-sm">
            <BookOpen className="size-4 text-signal-bright" />
            {current.source}
          </p>
        </div>
        <div className="mt-8">
          <span className="label-caps text-paper-muted">Evidence sequence</span>
          <div className="mt-3 flex gap-1">
            {replay.map((_, index) => (
              <span
                key={index}
                className={cn(
                  "h-1 flex-1 bg-paper-dim transition-colors duration-500",
                  index <= safeStep && "bg-signal-bright",
                )}
              />
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

function LearningView({ snapshot }: { snapshot: LevySnapshot }) {
  const { calibration, resolved, brierScore } = snapshot;
  const alignment = useMemo(() => {
    if (calibration.length === 0) return 0;
    const meanAbsErr =
      calibration.reduce((sum, point) => sum + Math.abs(point.forecast - point.actual), 0) /
      calibration.length;
    return Math.max(0, Math.round(100 - meanAbsErr));
  }, [calibration]);
  const resolvedCount = resolved.length;

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
      <section className="border border-border bg-surface p-5">
        <div className="flex items-start justify-between">
          <div>
            <span className="label-caps text-signal">Learning system</span>
            <h2 className="mt-2 font-display text-2xl font-semibold">Calibration curve</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Forecast confidence compared with observed frequency.
            </p>
          </div>
          <ShieldCheck className="size-5 text-positive" />
        </div>
        {calibration.length === 0 ? (
          <EmptyPanel label="No calibration data yet" />
        ) : (
          <div className="mt-6 h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={calibration} margin={{ top: 10, right: 10, bottom: 0, left: -15 }}>
                <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="2 4" />
                <XAxis
                  dataKey="forecast"
                  domain={[0, 100]}
                  type="number"
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
                />
                <YAxis
                  domain={[0, 100]}
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
                />
                <ReferenceLine
                  segment={[
                    { x: 0, y: 0 },
                    { x: 100, y: 100 },
                  ]}
                  stroke="var(--muted-foreground)"
                  strokeDasharray="4 4"
                />
                <Line
                  type="monotone"
                  dataKey="actual"
                  stroke="var(--positive)"
                  strokeWidth={2.5}
                  dot={{ fill: "var(--positive)", r: 4, strokeWidth: 0 }}
                  animationDuration={900}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        <div className="grid grid-cols-3 border-t border-border pt-4">
          <div>
            <span className="label-caps text-muted-foreground">Brier score</span>
            <p className="mt-1 font-display text-2xl font-semibold">{brierScore.toFixed(2)}</p>
          </div>
          <div>
            <span className="label-caps text-muted-foreground">Resolved</span>
            <p className="mt-1 font-display text-2xl font-semibold">
              {resolvedCount.toLocaleString()}
            </p>
          </div>
          <div>
            <span className="label-caps text-muted-foreground">Alignment</span>
            <p className="mt-1 font-display text-2xl font-semibold">{alignment}%</p>
          </div>
        </div>
      </section>
      <section className="border border-border bg-surface">
        <div className="border-b border-border px-5 py-4">
          <span className="label-caps text-muted-foreground">Resolution ledger</span>
          <h2 className="mt-1 font-display text-lg font-semibold">Recent scored forecasts</h2>
        </div>
        {resolved.length === 0 ? (
          <EmptyPanel label="No resolved forecasts yet" />
        ) : (
          <div className="divide-y divide-border">
            {resolved.map((item) => (
              <div
                key={item.event}
                className="grid grid-cols-[1fr_auto_auto] items-center gap-4 px-5 py-4"
              >
                <div>
                  <p className="text-sm font-medium">{item.event}</p>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    Resolved · independently checked
                  </p>
                </div>
                <div className="text-right">
                  <span className="label-caps text-muted-foreground">Forecast</span>
                  <p className="font-mono text-sm font-bold">{item.probability}%</p>
                </div>
                <span
                  className={cn("tag", item.outcome === "Occurred" ? "text-positive" : "text-risk")}
                >
                  {item.outcome}
                </span>
              </div>
            ))}
          </div>
        )}
        {snapshot.source === "demo" && (
          <div className="m-5 border border-warning/25 bg-warning-soft p-4 text-xs leading-5 text-warning">
            <strong>Demo note:</strong> confidence grades and calibration figures are illustrative,
            not statistically validated production results.
          </div>
        )}
      </section>
    </div>
  );
}

export function LevyDesk({ snapshot: initialSnapshot }: { snapshot: LevySnapshot }) {
  const snapshot = useLiveSnapshot(initialSnapshot);
  const isLive = snapshot.source === "atlas";

  return (
    <TooltipProvider delayDuration={200}>
      <main className="min-h-screen bg-background text-foreground">
        <header className="sticky top-0 z-40 border-b border-border bg-background/95 backdrop-blur">
          <div className="mx-auto flex h-16 max-w-[1480px] items-center justify-between px-4 sm:px-6">
            <div className="flex items-center gap-5">
              <div className="flex items-center gap-2">
                <div className="grid size-8 place-items-center bg-foreground text-background">
                  <Globe2 className="size-4" />
                </div>
                <span className="font-display text-xl font-bold tracking-normal">LEVY</span>
              </div>
              <span className="hidden border-l border-border pl-5 text-xs text-muted-foreground md:block">
                Tariff Intelligence Desk
              </span>
            </div>
            <div className="flex items-center gap-3">
              <span className="hidden items-center gap-2 text-[10px] font-semibold uppercase text-muted-foreground sm:flex">
                <span className="relative flex size-2">
                  <span
                    className={cn(
                      "absolute inline-flex size-full animate-ping rounded-full opacity-60",
                      isLive ? "bg-positive" : "bg-warning",
                    )}
                  />
                  <span
                    className={cn(
                      "relative inline-flex size-2 rounded-full",
                      isLive ? "bg-positive" : "bg-warning",
                    )}
                  />
                </span>
                {isLive ? "Live from Atlas" : "System observing"}
              </span>
              <span
                className={cn(
                  "border px-2 py-1 font-mono text-[9px] font-bold uppercase",
                  isLive
                    ? "border-positive/30 bg-positive-soft text-positive"
                    : "border-warning/30 bg-warning-soft text-warning",
                )}
              >
                {isLive ? "Live data" : "Demo mode"}
              </span>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" aria-label="Activity status">
                    <Activity />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {isLive
                    ? "Streaming live updates from the agent service"
                    : "Demo data — set LEVY_API_URL to go live"}
                </TooltipContent>
              </Tooltip>
            </div>
          </div>
        </header>
        <div className="mx-auto max-w-[1480px] px-4 py-5 sm:px-6 sm:py-7">
          <div className="mb-6 flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
            <div>
              <div className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase text-muted-foreground">
                <Clock3 className="size-3" />
                {isLive ? "Live snapshot · Atlas" : "Snapshot · demo data"}
              </div>
              <h1 className="max-w-4xl font-display text-3xl font-semibold leading-[1.05] sm:text-5xl">
                Trade risk, translated into decisions.
              </h1>
            </div>
            <p className="max-w-md text-xs leading-5 text-muted-foreground lg:text-right">
              Signals are filtered, cited, and tied directly to every forecast movement.
            </p>
          </div>
          <Tabs defaultValue="command">
            <TabsList className="mb-4 h-auto w-full justify-start gap-0 rounded-none border-y border-border bg-transparent p-0">
              <TabsTrigger
                value="command"
                className="rounded-none border-r border-border px-4 py-3 text-xs shadow-none data-[state=active]:bg-foreground data-[state=active]:text-background"
              >
                <Radar /> Command
              </TabsTrigger>
              <TabsTrigger
                value="replay"
                className="rounded-none border-r border-border px-4 py-3 text-xs shadow-none data-[state=active]:bg-foreground data-[state=active]:text-background"
              >
                <Play /> Canada replay
              </TabsTrigger>
              <TabsTrigger
                value="learning"
                className="rounded-none border-r border-border px-4 py-3 text-xs shadow-none data-[state=active]:bg-foreground data-[state=active]:text-background"
              >
                <BarChart3 /> Learning
              </TabsTrigger>
              <TabsTrigger
                value="ask"
                className="rounded-none px-4 py-3 text-xs shadow-none data-[state=active]:bg-foreground data-[state=active]:text-background"
              >
                <Sparkles /> Ask LEVY
              </TabsTrigger>
            </TabsList>
            <TabsContent value="command" className="mt-0">
              <CommandView snapshot={snapshot} />
            </TabsContent>
            <TabsContent value="replay" className="mt-0">
              <ReplayView replay={snapshot.replay} />
            </TabsContent>
            <TabsContent value="learning" className="mt-0">
              <LearningView snapshot={snapshot} />
            </TabsContent>
            <TabsContent value="ask" className="mt-0">
              <LevyChat />
            </TabsContent>
          </Tabs>
          <footer className="mt-8 flex flex-col justify-between gap-2 border-t border-border py-5 text-[10px] uppercase text-muted-foreground sm:flex-row">
            <span>LEVY / Decision intelligence for trade policy</span>
            <span className="flex items-center gap-1.5">
              <Check className="size-3 text-positive" /> Evidence trail preserved
            </span>
          </footer>
        </div>
      </main>
    </TooltipProvider>
  );
}
