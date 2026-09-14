import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface HealthResponse {
  status: string;
  db: boolean;
  pgvector: boolean;
  extensions: Record<string, boolean>;
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const checkHealth = () => {
    setLoading(true);
    setError(null);
    fetch("/api/health")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: HealthResponse) => setHealth(data))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    checkHealth();
  }, []);

  return (
    <main className="mx-auto flex min-h-screen max-w-lg flex-col items-center justify-center gap-6 p-6">
      <h1 className="text-2xl font-semibold">Kivi Semantic Memory</h1>

      <div className="w-full rounded-lg border border-border p-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm text-muted-foreground">/health</span>
          <Button size="sm" variant="outline" onClick={checkHealth} disabled={loading}>
            {loading ? "Checking..." : "Recheck"}
          </Button>
        </div>

        {error && <p className="text-sm text-red-600">Request failed: {error}</p>}

        {health && (
          <div className="space-y-2 text-sm">
            <Row label="status" ok={health.status === "ok"} value={health.status} />
            <Row label="db" ok={health.db} value={String(health.db)} />
            <Row label="pgvector" ok={health.pgvector} value={String(health.pgvector)} />
            {Object.entries(health.extensions).map(([name, present]) => (
              <Row key={name} label={`extension: ${name}`} ok={present} value={String(present)} />
            ))}
          </div>
        )}
      </div>
    </main>
  );
}

function Row({ label, ok, value }: { label: string; ok: boolean; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("font-mono", ok ? "text-green-600" : "text-red-600")}>{value}</span>
    </div>
  );
}
