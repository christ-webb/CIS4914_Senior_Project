import type { Span } from "./types.ts";

export const NODE_W = 230;
export const NODE_H = 86;
const GAP_X = 90;
const GAP_Y = 34;

export interface SpanLayout {
  positions: Record<string, { x: number; y: number }>;
  edges: { id: string; source: string; target: string }[];
}

export function layoutSpans(spans: Span[]): SpanLayout {
  const ids = new Set(spans.map((span) => span.span_id));
  const parentOf = new Map<string, string>();
  spans.forEach((span, index) => {
    const parent = span.parent_span_id ?? (index > 0 ? spans[index - 1].span_id : null);
    if (parent && parent !== span.span_id && ids.has(parent)) parentOf.set(span.span_id, parent);
  });

  const children = new Map<string, string[]>();
  for (const span of spans) {
    const parent = parentOf.get(span.span_id);
    if (parent) children.set(parent, [...(children.get(parent) ?? []), span.span_id]);
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

  for (const span of spans) if (!parentOf.has(span.span_id)) place(span.span_id, 0);
  for (const span of spans) if (!visited.has(span.span_id)) place(span.span_id, 0);

  const edges = [...parentOf].map(([target, source]) => ({
    id: `${source}-${target}`,
    source,
    target,
  }));
  return { positions, edges };
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
  const origin = Math.min(...starts);
  const totalMs = Math.max(1, ...spans.map((span, i) => starts[i] - origin + span.duration_ms));
  const rows = spans
    .map((span, i) => ({ span, offsetMs: starts[i] - origin }))
    .sort((a, b) => a.offsetMs - b.offsetMs)
    .map(({ span, offsetMs }) => ({
      span,
      offsetMs,
      offsetPct: (offsetMs / totalMs) * 100,
      widthPct: Math.max(0.6, (span.duration_ms / totalMs) * 100),
    }));
  return { rows, totalMs };
}
