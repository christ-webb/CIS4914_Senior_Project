import { Suspense } from "react";

import { TraceExplorer } from "@/components/traces/trace-explorer";

export default function ExplorerPage() {
  return (
    <Suspense fallback={null}>
      <TraceExplorer />
    </Suspense>
  );
}
