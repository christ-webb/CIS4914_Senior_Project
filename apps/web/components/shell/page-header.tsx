"use client";
import { useState } from "react";
import type { ReactNode } from "react";

/** Page titles are one or two words; functional metadata belongs in `actions`, never a subtitle. */
export function PageHeader({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <header className="page-head">
      <h1 className="page-title"><span className="title-mark" aria-hidden="true" />{title}</h1>
      {actions && <div className="toolbar-actions">{actions}</div>}
    </header>
  );
}

export function ArrowIcon() {
  return <svg className="arrow-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 8h11M9 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" /></svg>;
}

export function LiveStatus({
  online,
  updatedAt,
  onRefresh,
}: {
  online: boolean;
  updatedAt: Date | null;
  onRefresh: () => void | Promise<void>;
}) {
  const [refreshing, setRefreshing] = useState(false);
  const [refreshed, setRefreshed] = useState(false);
  return (
    <>
      <div className={online ? "api-status online" : "api-status offline"} role="status">
        <span className="dot" />
        {online ? <>Live{updatedAt && <span className="api-time">{updatedAt.toLocaleTimeString()}</span>}</> : "API unreachable · retrying"}
      </div>
      <button className="btn btn-outline" disabled={refreshing} onClick={async () => { setRefreshing(true); setRefreshed(false); try { await onRefresh(); setRefreshed(true); } finally { setRefreshing(false); } }}>
        <svg className="btn-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M13.5 8A5.5 5.5 0 1 1 11.9 4M12 1.5V4.5H9" fill="none" stroke="currentColor" strokeWidth="1.7" /></svg>
        {refreshing ? "Refreshing…" : "Refresh"}
      </button>
      {refreshed && <span className="refresh-note" role="status">{online ? "Updated just now" : "Refresh failed. Try again."}</span>}
    </>
  );
}

export function EmptyState({ online, children }: { online: boolean; children?: ReactNode }) {
  return (
    <section className="empty-state">
      <span className="empty-mark" aria-hidden="true" />
      <h2>{online ? "No runs yet" : "API offline"}</h2>
      <p>{online ? <>Install hooks with <code>uv run nm5-cursor-install-hooks</code></> : <>Start <code>make clickhouse</code> and <code>make api-dev</code></>}</p>
      {children}
    </section>
  );
}

export function SectionHead({ index, title, count, children }: { index?: string; title: string; count?: ReactNode; children?: ReactNode }) {
  return (
    <div className="section-head">
      {index && <span className="section-index" aria-hidden="true">{index}</span>}
      <h2>{title}</h2>
      {count !== undefined && <span className="section-count">{count}</span>}
      {children && <div className="section-actions">{children}</div>}
    </div>
  );
}
