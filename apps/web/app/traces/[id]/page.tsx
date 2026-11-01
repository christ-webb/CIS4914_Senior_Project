import { TraceDetailView } from "@/components/traces/trace-detail-view";

export default async function TracePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <TraceDetailView traceId={id} />;
}
