import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { draftWithContext, type DraftResponse } from "@/lib/api";

export default function DraftPanel() {
  const [request, setRequest] = useState("");
  const [result, setResult] = useState<DraftResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const r = request.trim();
    if (!r || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await draftWithContext(r));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Ask Kivi to draft something. It writes using project state you've dictated but didn't
        restate here.
      </p>
      <Textarea
        value={request}
        onChange={(e) => setRequest(e.target.value)}
        placeholder="e.g. Draft a status update on Tara for leadership"
        rows={3}
      />
      <Button onClick={submit} disabled={loading || !request.trim()}>
        {loading ? "Drafting…" : "Draft"}
      </Button>

      {error && <p className="text-sm text-red-600">{error}</p>}

      {result && (
        <Card className="p-4">
          <p className="whitespace-pre-wrap text-sm">{result.draft}</p>
          {result.cited_memories.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5 border-t border-border pt-3">
              {result.cited_memories.map((m) => (
                <Badge key={m.id} variant="outline" title={m.claim}>
                  #{m.id}
                </Badge>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
