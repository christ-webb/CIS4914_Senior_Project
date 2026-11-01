import type { ReactNode } from "react";

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow: string;
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="toolbar-actions">{actions}</div>}
    </header>
  );
}

export function LiveStatus({
  online,
  updatedAt,
  onRefresh,
}: {
  online: boolean;
  updatedAt: Date | null;
  onRefresh: () => void;
}) {
  return (
    <>
      <div className={online ? "api-status online" : "api-status offline"} role="status">
        <span className="dot" />
        {online ? `Live${updatedAt ? ` · ${updatedAt.toLocaleTimeString()}` : ""}` : "API unreachable · retrying"}
      </div>
      <button className="btn" onClick={onRefresh}>Refresh</button>
    </>
  );
}

export function EmptyState({ online, children }: { online: boolean; children?: ReactNode }) {
  return (
    <section className="empty-state">
      <h2>{online ? "No traces yet" : "Can't reach the API"}</h2>
      <p>
        {online ? "Send your first trace from the demo agent." : "Start the API on port 8000."} Try <code>make demo</code>.
      </p>
      {children}
    </section>
  );
}
