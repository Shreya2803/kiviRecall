import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { fetchDictations, type Dictation } from "@/lib/api";

function formatWhen(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function RememberedBadge({ d }: { d: Dictation }) {
  if (d.memories_created > 0) {
    return (
      <Badge variant="success" title={d.reason ?? undefined}>
        remembered · {d.memories_created}
      </Badge>
    );
  }
  return (
    <Badge variant="muted" title={d.reason ?? "not yet processed"}>
      nothing kept
    </Badge>
  );
}

export default function DictationFeed() {
  const [dictations, setDictations] = useState<Dictation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    setError(null);
    fetchDictations(100)
      .then(setDictations)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Dictation feed</h2>
          <p className="text-sm text-muted-foreground">
            Everything you've dictated, and whether Kivi kept anything from it.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </Button>
      </div>

      {error && <p className="text-sm text-red-600">Couldn't load the feed: {error}</p>}
      {dictations && dictations.length === 0 && (
        <p className="text-sm text-muted-foreground">No dictations imported yet.</p>
      )}

      <div className="space-y-2">
        {dictations?.map((d) => (
          <Card key={d.id} className="p-4">
            <div className="mb-2 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span>{formatWhen(d.spoken_at)}</span>
                {d.app_context && <Badge variant="outline">{d.app_context}</Badge>}
              </div>
              <RememberedBadge d={d} />
            </div>
            <p className="text-sm">{d.formatted_output}</p>
          </Card>
        ))}
      </div>
    </div>
  );
}
