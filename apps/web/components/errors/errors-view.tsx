"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { EmptyState, LiveStatus, PageHeader } from "@/components/shell/page-header";
import { usePolling } from "@/hooks/use-polling";
import { fetchFullTraces } from "@/lib/api";
import { extractErrors, groupErrors, type ErrorGroup } from "@/lib/analytics";
import { formatMs } from "@/lib/stats";

export function ErrorsView() {
  const params = useSearchParams();
  const { data, online, loading, updatedAt, refresh } = usePolling(fetchFullTraces, 15000);
  const [picked, setPicked] = useState<string | null>(params.get("group"));
  const [query, setQuery] = useState("");

  const traces = useMemo(() => data ?? [], [data]);
  const groups = useMemo(() => groupErrors(extractErrors(traces)), [traces]);
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return groups.filter((group) => !needle || `${group.name} ${group.message}`.toLowerCase().includes(needle));
  }, [groups, query]);
  const active = groups.find((group) => group.signature === picked) ?? shown[0] ?? null;
  const failedRuns = traces.filter((trace) => trace.status === "error").length;

  return (
    <>
      <PageHeader
        eyebrow="ERRORS"
        title="What's breaking, and where"
        subtitle={`${groups.length} distinct error${groups.length === 1 ? "" : "s"} across ${failedRuns} failed run${failedRuns === 1 ? "" : "s"}.`}
        actions={<LiveStatus online={online} updatedAt={updatedAt} onRefresh={refresh} />}
      />

      {!loading && traces.length === 0 ? (
        <EmptyState online={online} />
      ) : groups.length === 0 && !loading ? (
        <section className="empty-state"><h2>No errors 🎉</h2><p>None of the recent runs contain a failed span.</p></section>
      ) : (
        <section className="explorer">
          <aside className="card run-list" aria-label="Error groups">
            <h3>Errors</h3>
            <input className="search" type="search" placeholder="Search errors…" aria-label="Search errors" value={query} onChange={(event) => setQuery(event.target.value)} />
            <div className="run-scroll">
              {shown.length === 0 && <p className="muted">No errors match.</p>}
              {shown.map((group) => (
                <button key={group.signature} className={active?.signature === group.signature ? "run active" : "run"} onClick={() => setPicked(group.signature)}>
                  <span className="run-title"><i className="dot error" /><span>{group.name}</span><span className="count">{group.count}</span></span>
                  <small className="muted clamp">{group.message}</small>
                  <small className="muted">last seen {new Date(group.lastSeen).toLocaleTimeString()}</small>
                </button>
              ))}
            </div>
          </aside>
          <div className="card trace-panel">{active ? <ErrorDetail group={active} /> : <p className="muted">Select an error to see its details.</p>}</div>
        </section>
      )}
    </>
  );
}

function ErrorDetail({ group }: { group: ErrorGroup }) {
  const latest = group.events[0];
  return (
    <div className="error-detail">
      <p className="eyebrow">{group.type.toUpperCase()} ERROR</p>
      <h2>{group.name}</h2>
      <p className="error-message">{group.message}</p>

      <dl className="mini-stats">
        <div><dt>Occurrences</dt><dd>{group.count}</dd></div>
        <div><dt>Affected runs</dt><dd>{group.traceIds.length}</dd></div>
        <div><dt>First seen</dt><dd>{new Date(group.firstSeen).toLocaleString()}</dd></div>
        <div><dt>Last seen</dt><dd>{new Date(group.lastSeen).toLocaleString()}</dd></div>
      </dl>

      <h4>Failure path (latest occurrence)</h4>
      <ol className="path">
        {latest.path.map((span, index) => (
          <li key={span.span_id} className={index === latest.path.length - 1 ? "failed" : ""}>
            <span className={`type-pill t-${span.type}`}>{span.type}</span> {span.name}
            <small className="muted"> {formatMs(span.duration_ms)}</small>
          </li>
        ))}
      </ol>

      <h4>Latest payload</h4>
      <div className="payload">
        <Payload label="Input" value={latest.span.input} />
        <Payload label="Output" value={latest.span.output} />
        <Payload label="Attributes" value={Object.keys(latest.span.attributes).length ? latest.span.attributes : null} />
      </div>

      <h4>Occurrences</h4>
      <table className="table">
        <thead><tr><th>When</th><th>Run</th><th>Project</th><th>Duration</th></tr></thead>
        <tbody>
          {group.events.slice(0, 20).map((event) => (
            <tr key={event.span.span_id}>
              <td className="muted">{new Date(event.at).toLocaleString()}</td>
              <td><Link href={`/traces/${event.traceId}`} className="row-link">{event.traceName}</Link></td>
              <td className="muted">{event.projectId}</td>
              <td className="num">{formatMs(event.span.duration_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Payload({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <span className="kpi-label">{label}</span>
      <pre>{value == null ? "—" : JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}
