import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchMemories,
  confirmMemory,
  forgetMemory,
  correctMemory,
  type MemoriesResponse,
  type MemoryItem,
} from "@/lib/api";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function MemoryCard({ memory, onChanged }: { memory: MemoryItem; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draftClaim, setDraftClaim] = useState(memory.claim);
  const [showHistory, setShowHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm">{memory.claim}</p>
        <div className="flex shrink-0 gap-1">
          {memory.pinned && <Badge variant="outline">pinned</Badge>}
          {memory.origin === "user" && <Badge variant="outline">user-corrected</Badge>}
          <Badge variant="muted">{(memory.confidence * 100).toFixed(0)}% confidence</Badge>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>first learned {formatDate(memory.first_learned)}</span>
        <span>
          last confirmed{" "}
          {memory.last_confirmed_at ? formatDate(memory.last_confirmed_at) : "never explicitly"}
        </span>
        <span>
          {memory.supporting_dictation_count} supporting dictation
          {memory.supporting_dictation_count === 1 ? "" : "s"}
        </span>
        {memory.history.length > 0 && (
          <button className="underline underline-offset-2" onClick={() => setShowHistory((s) => !s)}>
            {memory.history.length} earlier version{memory.history.length === 1 ? "" : "s"}
          </button>
        )}
      </div>

      {showHistory && (
        <ul className="mt-2 space-y-1 border-l-2 border-border pl-3 text-xs text-muted-foreground">
          {memory.history.map((h) => (
            <li key={h.id}>
              <span className="font-mono">{formatDate(h.valid_from)}</span> — {h.claim}
            </li>
          ))}
        </ul>
      )}

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {editing ? (
        <div className="mt-3 space-y-2">
          <Textarea value={draftClaim} onChange={(e) => setDraftClaim(e.target.value)} rows={2} />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={busy || !draftClaim.trim()}
              onClick={() =>
                run(async () => {
                  await correctMemory(memory.id, draftClaim.trim(), "corrected via What Kivi knows");
                  setEditing(false);
                })
              }
            >
              Save correction
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => run(() => confirmMemory(memory.id, "confirmed via What Kivi knows"))}
          >
            Confirm
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setEditing(true)}>
            Correct
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => run(() => forgetMemory(memory.id, "forgotten via What Kivi knows"))}
          >
            Forget
          </Button>
        </div>
      )}
    </Card>
  );
}

export default function WhatKiviKnows() {
  const [data, setData] = useState<MemoriesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    fetchMemories()
      .then(setData)
      .catch((e: Error) => setError(e.message));
  };

  useEffect(load, []);

  return (
    <div className="mx-auto max-w-2xl space-y-6 p-6">
      <div>
        <h2 className="text-lg font-semibold">What Kivi knows</h2>
        <p className="text-sm text-muted-foreground">
          Review, confirm, correct, or forget anything Kivi has learned from your dictations.
        </p>
      </div>

      <Card className="border-dashed p-4 text-sm">
        <p className="font-medium">Kivi remembers your work, not you.</p>
        <p className="mt-1 text-muted-foreground">
          It never keeps your mood, health, opinions about colleagues, or financial or family
          detail — no matter how it's phrased. There is nothing to review here for any of that,
          by design.
        </p>
      </Card>

      {error && <p className="text-sm text-red-600">Couldn't load memory: {error}</p>}

      {data?.groups.length === 0 && data.general.length === 0 && (
        <p className="text-sm text-muted-foreground">Nothing learned yet.</p>
      )}

      {data?.groups.map((g) => (
        <div key={g.entity_id} className="space-y-2">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">{g.canonical_name}</h3>
            <Badge variant="outline">{g.entity_type}</Badge>
            {g.needs_review && <Badge variant="muted">unconfirmed entity</Badge>}
          </div>
          <div className="space-y-2">
            {g.memories.map((m) => (
              <MemoryCard key={m.id} memory={m} onChanged={load} />
            ))}
          </div>
        </div>
      ))}

      {data && data.general.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">General</h3>
          <div className="space-y-2">
            {data.general.map((m) => (
              <MemoryCard key={m.id} memory={m} onChanged={load} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
