import { TraceExplorer } from "@/components/traces/trace-explorer";
import { listTraces } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ExplorerPage() {
  const traces = await listTraces();

  return (
    <div className="classic-explorer">
      <header className="page-head">
        <div>
          <p className="eyebrow">NO MORE 500S</p>
          <h1>Agent traces</h1>
          <p className="muted">Follow every observable model and tool step from request to result.</p>
        </div>
      </header>
      <TraceExplorer initialTraces={traces} />
    </div>
  );
}
