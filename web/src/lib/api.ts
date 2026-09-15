export interface Dictation {
  id: string;
  spoken_at: string;
  formatted_output: string;
  app_context: string | null;
  window_title: string | null;
  language: string[] | null;
  memories_created: number;
  outcome: string | null;
  reason: string | null;
}

export interface MemoryHistoryEntry {
  id: number;
  claim: string;
  valid_from: string;
  valid_to: string | null;
}

export interface MemoryItem {
  id: number;
  memory_type: string;
  claim: string;
  confidence: number;
  origin: "extracted" | "user";
  pinned: boolean;
  occurrence_count: number;
  first_learned: string;
  last_confirmed_at: string | null;
  supporting_dictation_count: number;
  history: MemoryHistoryEntry[];
}

export interface MemoryGroup {
  entity_id: number;
  canonical_name: string;
  entity_type: string;
  needs_review: boolean;
  memories: MemoryItem[];
}

export interface MemoriesResponse {
  groups: MemoryGroup[];
  general: MemoryItem[];
}

export interface CitedMemory {
  id: number;
  claim: string;
  confidence?: number;
  memory_type?: string;
  source_dictation_ids?: string[];
}

export interface AskStageEvent {
  type: "stage";
  stage: string;
  [key: string]: unknown;
}

export interface AskAnswerChunkEvent {
  type: "answer_chunk";
  text: string;
}

export interface AskDoneEvent {
  type: "done";
  answer: string;
  route: string;
  sufficiency_verdict: string;
  sufficiency_reason: string;
  cited_memories: CitedMemory[];
  trace_id: number | null;
  cost_usd: number;
  latency_ms: number;
}

export type AskEvent = AskStageEvent | AskAnswerChunkEvent | AskDoneEvent;

export async function fetchDictations(limit = 50): Promise<Dictation[]> {
  const res = await fetch(`/api/dictations?limit=${limit}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchMemories(): Promise<MemoriesResponse> {
  const res = await fetch("/api/memories");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function postAction(path: string, body: Record<string, unknown>): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
}

export function confirmMemory(id: number, reason?: string): Promise<void> {
  return postAction(`/api/memories/${id}/confirm`, { reason: reason ?? null });
}

export function forgetMemory(id: number, reason?: string): Promise<void> {
  return postAction(`/api/memories/${id}/forget`, { reason: reason ?? null });
}

export function correctMemory(id: number, claim: string, reason?: string): Promise<void> {
  return postAction(`/api/memories/${id}/correct`, { claim, reason: reason ?? null });
}

export interface DraftResponse {
  draft: string;
  cited_memories: { id: number; claim: string }[];
  cost_usd: number;
}

export async function draftWithContext(request: string): Promise<DraftResponse> {
  const res = await fetch("/api/draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** Parses one "event: X\ndata: {...}" SSE frame at a time from /api/ask. */
export async function askQuestion(
  question: string,
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const eventLine = frame.split("\n").find((l) => l.startsWith("event:"));
      const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!eventLine || !dataLine) continue;
      const type = eventLine.slice("event:".length).trim();
      const data = JSON.parse(dataLine.slice("data:".length).trim());
      onEvent({ type, ...data } as AskEvent);
    }
  }
}
