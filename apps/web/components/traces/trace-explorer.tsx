"use client";

import { useEffect, useState } from "react";

import { TraceGraph } from "@/components/traces/trace-graph";
import { getTrace } from "@/lib/api";
import type { Trace, TraceSummary } from "@/lib/types";

export function TraceExplorer({ initialTraces }: { initialTraces: TraceSummary[] }) {
  const [selected, setSelected] = useState<Trace | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!initialTraces[0]) return;
    getTrace(initialTraces[0].trace_id).then(setSelected).catch(() => setError("Could not load trace."));
  }, [initialTraces]);

  async function selectTrace(traceId: string) {
    setError(null);
    try {
      setSelected(await getTrace(traceId));
    } catch {
      setError("Could not load trace.");
    }
  }

  if (initialTraces.length === 0) {
    return (
      <section className="empty-state">
        <h2>No traces yet</h2>
        <p>Start the API, run <code>make demo</code>, then refresh this page.</p>
      </section>
    );
  }

  return (
    <section className="explorer">
      <aside className="run-list" aria-label="Agent runs">
        <h2>Runs</h2>
        {initialTraces.map((trace) => (
          <button
            className={selected?.trace_id === trace.trace_id ? "run active" : "run"}
            key={trace.trace_id}
            onClick={() => selectTrace(trace.trace_id)}
          >
            <span>{trace.name}</span>
            <small>{trace.duration_ms.toFixed(0)} ms · {trace.total_tokens} tokens</small>
          </button>
        ))}
      </aside>
      <div className="trace-panel">
        {error && <p role="alert">{error}</p>}
        {selected ? <TraceGraph trace={selected} /> : <p>Loading trace…</p>}
      </div>
    </section>
  );
}

