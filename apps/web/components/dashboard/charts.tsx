"use client";

import { useId } from "react";

import type { SpanStatus, TraceSummary } from "@/lib/types";
import { formatMs } from "@/lib/stats";

export function Sparkline({ values, color = "var(--accent)" }: { values: number[]; color?: string }) {
  const id = useId();
  if (values.length < 2) return <div className="spark-empty" aria-hidden />;
  const w = 120;
  const h = 36;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 4 - ((v - min) / range) * (h - 8)]);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity=".35" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L${w} ${h} L0 ${h} Z`} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

const DONUT_COLORS: Record<SpanStatus, string> = {
  ok: "var(--ok)",
  error: "var(--bad)",
  unset: "var(--idle)",
};

export function Donut({ counts }: { counts: Record<SpanStatus, number> }) {
  const total = counts.ok + counts.error + counts.unset;
  const r = 15.9;
  let offset = 0;
  return (
    <svg className="donut" viewBox="0 0 36 36" role="img" aria-label="Run status distribution">
      <circle cx="18" cy="18" r={r} fill="none" stroke="var(--line)" strokeWidth="4" />
      {total > 0 &&
        (Object.keys(counts) as SpanStatus[]).map((key) => {
          const pct = (counts[key] / total) * 100;
          const circle = (
            <circle
              key={key}
              cx="18"
              cy="18"
              r={r}
              fill="none"
              stroke={DONUT_COLORS[key]}
              strokeWidth="4"
              strokeDasharray={`${pct} ${100 - pct}`}
              strokeDashoffset={-offset}
              transform="rotate(-90 18 18)"
            />
          );
          offset += pct;
          return pct > 0 ? circle : null;
        })}
    </svg>
  );
}

export function LatencyBars({
  traces,
  selectedId,
  onSelect,
}: {
  traces: TraceSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const max = Math.max(1, ...traces.map((trace) => trace.duration_ms));
  return (
    <div className="bars" role="list">
      {traces.map((trace) => (
        <button
          key={trace.trace_id}
          role="listitem"
          className={`bar ${trace.status}${selectedId === trace.trace_id ? " selected" : ""}`}
          style={{ height: `${Math.max(6, (trace.duration_ms / max) * 100)}%` }}
          title={`${trace.name} · ${formatMs(trace.duration_ms)} · ${trace.status}`}
          aria-label={`${trace.name}, ${formatMs(trace.duration_ms)}, ${trace.status}`}
          onClick={() => onSelect(trace.trace_id)}
        />
      ))}
    </div>
  );
}
