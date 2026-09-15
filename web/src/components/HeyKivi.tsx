import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { askQuestion, type AskEvent, type CitedMemory } from "@/lib/api";
import DraftPanel from "@/components/DraftPanel";

interface Turn {
  question: string;
  answerText: string;
  stage: string;
  route?: string;
  verdict?: string;
  verdictReason?: string;
  citedMemories?: CitedMemory[];
  costUsd?: number;
  latencyMs?: number;
  done: boolean;
  showWhy: boolean;
}

const STAGE_LABEL: Record<string, string> = {
  parsing: "Reading the question…",
  retrieved: "Searching memory…",
  sufficiency: "Checking whether that's enough…",
};

function isAbstention(verdict?: string): boolean {
  return verdict === "insufficient";
}

export default function HeyKivi() {
  const [mode, setMode] = useState<"ask" | "draft">("ask");
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [asking, setAsking] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const ask = async () => {
    const q = question.trim();
    if (!q || asking) return;
    setQuestion("");
    setAsking(true);

    const index = turns.length;
    setTurns((prev) => [
      ...prev,
      { question: q, answerText: "", stage: "parsing", done: false, showWhy: false },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;

    const update = (patch: Partial<Turn>) =>
      setTurns((prev) => prev.map((t, i) => (i === index ? { ...t, ...patch } : t)));

    try {
      await askQuestion(
        q,
        (event: AskEvent) => {
          if (event.type === "stage") {
            const patch: Partial<Turn> = { stage: event.stage as string };
            if (typeof event.route === "string") patch.route = event.route;
            update(patch);
          } else if (event.type === "answer_chunk") {
            update({ answerText: event.text });
          } else if (event.type === "done") {
            update({
              answerText: event.answer,
              done: true,
              verdict: event.sufficiency_verdict,
              verdictReason: event.sufficiency_reason,
              citedMemories: event.cited_memories,
              costUsd: event.cost_usd,
              latencyMs: event.latency_ms,
            });
          }
        },
        controller.signal,
      );
    } catch (e) {
      update({ answerText: `Something went wrong: ${(e as Error).message}`, done: true, verdict: "error" });
    } finally {
      setAsking(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4 p-6">
      <div>
        <h2 className="text-lg font-semibold">Hey Kivi</h2>
        <p className="text-sm text-muted-foreground">
          Ask about a project, a decision, ownership, or a commitment you've dictated — or have
          Kivi draft something using what it remembers.
        </p>
      </div>

      <div className="flex gap-1 border-b border-border">
        {([
          { id: "ask", label: "Q&A" },
          { id: "draft", label: "Draft" },
        ] as const).map((m) => (
          <button
            key={m.id}
            onClick={() => setMode(m.id)}
            className={cn(
              "px-3 py-2 text-sm",
              mode === m.id ? "border-b-2 border-primary font-medium" : "text-muted-foreground",
            )}
          >
            {m.label}
          </button>
        ))}
      </div>

      {mode === "draft" ? (
        <DraftPanel />
      ) : (
        <>
      <div className="space-y-4">
        {turns.map((t, i) => (
          <div key={i} className="space-y-2">
            <p className="text-sm font-medium">{t.question}</p>
            <Card
              className={cn(
                "p-4",
                t.done && isAbstention(t.verdict) && "border-muted bg-muted/40",
              )}
            >
              {!t.done && (
                <p className="text-sm text-muted-foreground">{STAGE_LABEL[t.stage] ?? "Working…"}</p>
              )}
              <p className={cn("whitespace-pre-wrap text-sm", t.done && isAbstention(t.verdict) && "text-muted-foreground")}>
                {t.answerText}
                {!t.done && <span className="animate-pulse">▍</span>}
              </p>

              {t.done && t.citedMemories && t.citedMemories.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {t.citedMemories.map((m) => (
                    <Badge key={m.id} variant="outline" title={m.claim}>
                      #{m.id} {m.memory_type}
                    </Badge>
                  ))}
                </div>
              )}

              {t.done && (
                <div className="mt-3 border-t border-border pt-2">
                  <button
                    className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                    onClick={() =>
                      setTurns((prev) =>
                        prev.map((tt, ii) => (ii === i ? { ...tt, showWhy: !tt.showWhy } : tt)),
                      )
                    }
                  >
                    Why this answer?
                  </button>
                  {t.showWhy && (
                    <div className="mt-2 space-y-2 text-xs text-muted-foreground">
                      <p>
                        route: <span className="font-mono">{t.route}</span> · verdict:{" "}
                        <span className="font-mono">{t.verdict}</span>
                      </p>
                      {t.verdictReason && <p>{t.verdictReason}</p>}
                      {t.citedMemories && t.citedMemories.length > 0 ? (
                        <ul className="space-y-1">
                          {t.citedMemories.map((m) => (
                            <li key={m.id} className="rounded border border-border p-2">
                              <div className="flex items-center justify-between">
                                <span className="font-mono">#{m.id}</span>
                                {m.confidence !== undefined && (
                                  <span>confidence {(m.confidence * 100).toFixed(0)}%</span>
                                )}
                              </div>
                              <p className="mt-1 text-foreground">{m.claim}</p>
                              {m.source_dictation_ids && m.source_dictation_ids.length > 0 && (
                                <p className="mt-1">from {m.source_dictation_ids.join(", ")}</p>
                              )}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p>No memories were cited for this answer.</p>
                      )}
                      {t.costUsd !== undefined && (
                        <p>
                          cost ${t.costUsd.toFixed(6)} · {t.latencyMs}ms
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </Card>
          </div>
        ))}
      </div>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          ask();
        }}
      >
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask Kivi something…"
          disabled={asking}
        />
        <Button type="submit" disabled={asking || !question.trim()}>
          {asking ? "Asking…" : "Ask"}
        </Button>
      </form>
        </>
      )}
    </div>
  );
}
