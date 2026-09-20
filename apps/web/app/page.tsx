import { TraceExplorer } from "@/components/traces/trace-explorer";
import { listTraces } from "@/lib/api";

export default async function Home() {
  const traces = await listTraces();

  return (
    <main>
      <header className="page-header">
        <p className="eyebrow">NO MORE 500S</p>
        <h1>Agent traces</h1>
        <p>Follow every observable model and tool step from request to result.</p>
      </header>
      <TraceExplorer initialTraces={traces} />
    </main>
  );
}

