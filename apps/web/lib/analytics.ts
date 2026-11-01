import type { Span, SpanType, Trace } from "./types.ts";
import { percentile } from "./stats.ts";

export interface ErrorEvent {
  traceId: string;
  traceName: string;
  projectId: string;
  span: Span;
  message: string;
  signature: string;
  at: string;
  path: Span[];
}

export interface ErrorGroup {
  signature: string;
  name: string;
  type: SpanType;
  message: string;
  count: number;
  traceIds: string[];
  firstSeen: string;
  lastSeen: string;
  events: ErrorEvent[];
}

function asText(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (value && typeof value === "object") return JSON.stringify(value);
  return null;
}

export function errorMessage(span: Span): string {
  const candidates = [
    span.output?.error,
    span.output?.message,
    span.attributes["exception.message"],
    span.attributes.error,
  ];
  for (const candidate of candidates) {
    const text = asText(candidate);
    if (text) return text;
  }
  return `${span.name} failed`;
}

export function errorSignature(span: Span, message: string): string {
  return `${span.type}:${span.name}:${message.toLowerCase().replace(/\d+/g, "#").trim()}`;
}

export function spanPath(span: Span, spans: Span[]): Span[] {
  const byId = new Map(spans.map((item) => [item.span_id, item]));
  const path: Span[] = [];
  const seen = new Set<string>();
  let current: Span | undefined = span;
  while (current && !seen.has(current.span_id)) {
    seen.add(current.span_id);
    path.unshift(current);
    current = current.parent_span_id ? byId.get(current.parent_span_id) : undefined;
  }
  return path;
}

export function extractErrors(traces: Trace[]): ErrorEvent[] {
  return traces.flatMap((trace) =>
    trace.spans
      .filter((span) => span.status === "error")
      .map((span) => {
        const message = errorMessage(span);
        return {
          traceId: trace.trace_id,
          traceName: trace.name,
          projectId: trace.project_id,
          span,
          message,
          signature: errorSignature(span, message),
          at: span.started_at,
          path: spanPath(span, trace.spans),
        };
      }),
  );
}

export function groupErrors(events: ErrorEvent[]): ErrorGroup[] {
  const groups = new Map<string, ErrorGroup>();
  for (const event of [...events].sort((a, b) => Date.parse(b.at) - Date.parse(a.at))) {
    const existing = groups.get(event.signature);
    if (existing) {
      existing.count += 1;
      existing.events.push(event);
      if (!existing.traceIds.includes(event.traceId)) existing.traceIds.push(event.traceId);
      if (Date.parse(event.at) < Date.parse(existing.firstSeen)) existing.firstSeen = event.at;
    } else {
      groups.set(event.signature, {
        signature: event.signature,
        name: event.span.name,
        type: event.span.type,
        message: event.message,
        count: 1,
        traceIds: [event.traceId],
        firstSeen: event.at,
        lastSeen: event.at,
        events: [event],
      });
    }
  }
  return [...groups.values()].sort((a, b) => b.count - a.count || Date.parse(b.lastSeen) - Date.parse(a.lastSeen));
}

export interface TypeStat {
  type: SpanType;
  count: number;
  errors: number;
  avgMs: number;
  p95Ms: number;
  tokens: number;
  cost: number;
}

export function spanTypeStats(traces: Trace[]): TypeStat[] {
  const buckets = new Map<SpanType, Span[]>();
  for (const trace of traces) {
    for (const span of trace.spans) buckets.set(span.type, [...(buckets.get(span.type) ?? []), span]);
  }
  return [...buckets.entries()]
    .map(([type, spans]) => {
      const durations = spans.map((span) => span.duration_ms);
      return {
        type,
        count: spans.length,
        errors: spans.filter((span) => span.status === "error").length,
        avgMs: durations.reduce((a, b) => a + b, 0) / spans.length,
        p95Ms: percentile(durations, 95),
        tokens: spans.reduce((a, span) => a + span.token_count, 0),
        cost: spans.reduce((a, span) => a + span.cost_usd, 0),
      };
    })
    .sort((a, b) => b.count - a.count);
}

export function slowestSpans(traces: Trace[], limit: number): { trace: Trace; span: Span }[] {
  return traces
    .flatMap((trace) => trace.spans.map((span) => ({ trace, span })))
    .sort((a, b) => b.span.duration_ms - a.span.duration_ms)
    .slice(0, limit);
}

export interface ProjectStat {
  project: string;
  runs: number;
  errors: number;
  tokens: number;
  cost: number;
}

export function projectStats(traces: Trace[]): ProjectStat[] {
  const map = new Map<string, ProjectStat>();
  for (const trace of traces) {
    const stat = map.get(trace.project_id) ?? { project: trace.project_id, runs: 0, errors: 0, tokens: 0, cost: 0 };
    stat.runs += 1;
    stat.errors += trace.status === "error" ? 1 : 0;
    stat.tokens += trace.total_tokens;
    stat.cost += trace.total_cost_usd;
    map.set(trace.project_id, stat);
  }
  return [...map.values()].sort((a, b) => b.cost - a.cost);
}

export interface TimeBucket {
  start: number;
  runs: number;
  errors: number;
}

export function timeBuckets(traces: Trace[], count: number): TimeBucket[] {
  if (traces.length === 0) return [];
  const times = traces.map((trace) => Date.parse(trace.started_at));
  const min = Math.min(...times);
  const width = (Math.max(...times) - min) / count || 1;
  const buckets: TimeBucket[] = Array.from({ length: count }, (_, i) => ({ start: min + i * width, runs: 0, errors: 0 }));
  traces.forEach((trace, i) => {
    const index = Math.min(count - 1, Math.floor((times[i] - min) / width));
    buckets[index].runs += 1;
    buckets[index].errors += trace.status === "error" ? 1 : 0;
  });
  return buckets;
}
