"use client";
import { TraceView } from "./trace-view";
import type { Trace } from "@/lib/types";
export function TraceGraph({ trace }: { trace: Trace }) {
  return <div className="graph-wrap"><TraceView key={trace.trace_id} trace={trace} /></div>;
}
