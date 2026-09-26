import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { ArrowUp, Bot, Loader2, Sparkles, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  "Which market carries the highest tariff risk right now, and why?",
  "Explain what moved the Canada steel forecast since June.",
  "Scenario: Canada raises steel tariffs to 60% next month. What happens?",
  "Scenario: the EU delays its EV duties by a year. Which forecasts move?",
  "Draft a 5-line briefing for a CFO on this week's findings.",
];

export function LevyChat() {
  const [input, setInput] = useState("");
  const { messages, sendMessage, status, stop, error } = useChat({
    transport: new DefaultChatTransport({ api: "/api/levy-chat" }),
  });
  const endRef = useRef<HTMLDivElement>(null);
  const busy = status === "submitted" || status === "streaming";

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  const submit = (text: string) => {
    const t = text.trim();
    if (!t || busy) return;
    sendMessage({ text: t });
    setInput("");
  };

  return (
    <section className="panel-enter grid min-h-[620px] border border-border bg-surface lg:grid-cols-[280px_1fr]">
      <aside className="border-b border-border p-5 lg:border-b-0 lg:border-r">
        <div className="label-caps mb-2 flex items-center gap-2 text-muted-foreground">
          <Sparkles className="size-3 text-signal" /> Analyst assistant
        </div>
        <h2 className="font-display text-2xl font-semibold leading-tight">Ask the desk.</h2>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">
          Make a request in plain language, or hand LEVY a what-if scenario. Answers explain each
          finding — and each simulation — with the forecasts, confidence, and cited evidence behind
          it.
        </p>
        <div className="mt-5 space-y-2">
          {SUGGESTIONS.map((s, i) => (
            <button
              key={s}
              type="button"
              disabled={busy}
              onClick={() => submit(s)}
              style={{ animationDelay: `${i * 70}ms` }}
              className="metric-enter block w-full border border-border bg-background px-3 py-2.5 text-left text-xs leading-5 transition hover:-translate-y-0.5 hover:border-foreground disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
      </aside>

      <div className="flex min-h-0 flex-col">
        <div className="max-h-[560px] flex-1 space-y-5 overflow-y-auto p-5">
          {messages.length === 0 && (
            <div className="flex h-full min-h-[320px] flex-col items-center justify-center text-center text-muted-foreground">
              <Bot className="mb-3 size-8 animate-pulse text-signal" />
              <p className="text-sm">Pick a suggestion or type your own request.</p>
            </div>
          )}
          {messages.map((m) => {
            const text = m.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
            const thinking = m.parts.some((p) => p.type === "reasoning") && !text;
            return (
              <div
                key={m.id}
                className={cn(
                  "metric-enter flex",
                  m.role === "user" ? "justify-end" : "justify-start",
                )}
              >
                <div
                  className={cn(
                    "max-w-[88%] px-4 py-3 text-sm leading-6",
                    m.role === "user"
                      ? "bg-foreground text-background"
                      : "border border-border bg-background",
                  )}
                >
                  {m.role === "assistant" && (
                    <div className="label-caps mb-1 text-signal">LEVY</div>
                  )}
                  {thinking ? (
                    <span className="flex items-center gap-2 text-muted-foreground">
                      <Loader2 className="size-3 animate-spin" /> Analyzing evidence…
                    </span>
                  ) : m.role === "assistant" ? (
                    <div className="prose prose-sm max-w-none text-foreground [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_table]:text-xs [&_th]:bg-surface [&_th]:text-left [&_td]:border [&_td]:border-border [&_td]:px-2 [&_th]:border [&_th]:border-border [&_th]:px-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-1.5">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
                    </div>
                  ) : (
                    text
                  )}
                </div>
              </div>
            );
          })}
          {status === "submitted" && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="size-3 animate-spin" /> Reviewing signals…
            </div>
          )}
          {error && (
            <div className="border border-risk bg-risk-soft px-3 py-2 text-xs text-risk">
              {error.message || "Something went wrong. Please try again."}
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit(input);
          }}
          className="flex items-end gap-2 border-t border-border p-3"
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={2}
            placeholder="Ask about a market, request a briefing, or pose a what-if scenario…"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(input);
              }
            }}
            className="min-h-[52px] flex-1 resize-none border border-border bg-background px-3 py-2 text-sm outline-none focus:border-foreground"
          />
          {busy ? (
            <Button
              type="button"
              size="icon"
              variant="outline"
              onClick={() => stop()}
              aria-label="Stop"
            >
              <Square />
            </Button>
          ) : (
            <Button type="submit" size="icon" disabled={!input.trim()} aria-label="Send">
              <ArrowUp />
            </Button>
          )}
        </form>
      </div>
    </section>
  );
}
