import type { Span } from "./types.ts";

export const NODE_W = 230;
export const NODE_H = 86;
const GAP_X = 90;
const GAP_Y = 34;

export interface SpanLayout {
  positions: Record<string, { x: number; y: number }>;
  edges: { id: string; source: string; target: string }[];
}

export function layoutGraph(ids: string[], parentOf: Map<string, string>): SpanLayout {
  const idSet = new Set(ids);
  const children = new Map<string, string[]>();
  const edges: SpanLayout["edges"] = [];
  for (const id of ids) {
    const parent = parentOf.get(id);
    if (!parent || !idSet.has(parent) || parent === id) continue;
    children.set(parent, [...(children.get(parent) ?? []), id]);
    edges.push({ id: `${parent}-${id}`, source: parent, target: id });
  }

  const positions: SpanLayout["positions"] = {};
  const visited = new Set<string>();
  let nextRow = 0;

  const place = (id: string, depth: number): number => {
    visited.add(id);
    const kids = (children.get(id) ?? []).filter((kid) => !visited.has(kid));
    let row: number;
    if (kids.length === 0) {
      row = nextRow++;
    } else {
      const rows = kids.map((kid) => place(kid, depth + 1));
      row = (rows[0] + rows[rows.length - 1]) / 2;
    }
    positions[id] = { x: depth * (NODE_W + GAP_X), y: row * (NODE_H + GAP_Y) };
    return row;
  };

  for (const id of ids) if (!parentOf.has(id) || !idSet.has(parentOf.get(id) ?? "")) place(id, 0);
  for (const id of ids) if (!visited.has(id)) place(id, 0);

  return { positions, edges };
}

export function layoutSpans(spans: Span[]): SpanLayout {
  const ids = new Set(spans.map((span) => span.span_id));
  const parentOf = new Map<string, string>();
  for (const span of spans) {
    const parent = span.parent_span_id;
    if (parent && parent !== span.span_id && ids.has(parent)) parentOf.set(span.span_id, parent);
  }
  return layoutGraph(
    spans.map((span) => span.span_id),
    parentOf,
  );
}

export function mergeLayoutPositions(
  previous: SpanLayout["positions"],
  next: SpanLayout,
): SpanLayout {
  const positions = { ...next.positions };
  for (const id of Object.keys(positions)) {
    if (previous[id]) positions[id] = previous[id];
  }
  return { ...next, positions };
}

export interface TimelineRow {
  span: Span;
  offsetMs: number;
  offsetPct: number;
  widthPct: number;
}

export function computeTimeline(spans: Span[]): { rows: TimelineRow[]; totalMs: number } {
  if (spans.length === 0) return { rows: [], totalMs: 0 };
  const starts = spans.map((span) => Date.parse(span.started_at));
  const finite = starts.filter((value) => Number.isFinite(value));
  const origin = finite.length ? Math.min(...finite) : 0;
  const totalMs = Math.max(
    1,
    ...spans.map((span, i) => {
      const start = Number.isFinite(starts[i]) ? starts[i] : origin;
      return start - origin + (span.duration_ms || 0);
    }),
  );
  const rows = spans
    .map((span, i) => ({
      span,
      offsetMs: (Number.isFinite(starts[i]) ? starts[i] : origin) - origin,
    }))
    .sort((a, b) => a.offsetMs - b.offsetMs || a.span.span_id.localeCompare(b.span.span_id))
    .map(({ span, offsetMs }) => ({
      span,
      offsetMs,
      offsetPct: (offsetMs / totalMs) * 100,
      widthPct: Math.max(0.6, ((span.duration_ms || 0) / totalMs) * 100),
    }));
  return { rows, totalMs };
}
