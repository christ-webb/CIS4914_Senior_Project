import type { Span, TraceSummary } from "./types.ts";
import { formatMs } from "./stats.ts";

type UsageRecord = Partial<TraceSummary> & { attributes?: Record<string, unknown> };
export function numeric(value: unknown): number | null {
  if ((typeof value !== "number" && typeof value !== "string") || (typeof value === "string" && !value.trim())) return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : null;
}
export function attribute(record: UsageRecord, key: string, fallback?: unknown): unknown {
  return record.attributes?.[key] ?? fallback;
}
export function currency(value: number): string {
  if (value > 0 && value < 0.000001) return "< $0.000001";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: value >= 0.01 || value === 0 ? 2 : 6 }).format(value);
}
export interface CostPresentation {
  kind: "point" | "range" | "partial" | "unavailable";
  label: string;
  qualification: string;
}
export function costPresentation(record: UsageRecord): CostPresentation {
  const get = (key: string, fallback?: unknown) => numeric(attribute(record, key, fallback));
  const status = attribute(record, "nm5.estimated_cost_status", record.estimated_cost_status);
  const amount = get("nm5.estimated_cost_usd", record.estimated_cost_usd);
  const details = record.attributes?.["nm5.pricing_details"] as Record<string, { amount_usd?: string }> | undefined;
  const standard = get("nm5.scenario_standard_usd", record.scenario_standard_usd ?? details?.standard?.amount_usd);
  const fast = get("nm5.scenario_fast_usd", record.scenario_fast_usd ?? details?.fast?.amount_usd);
  if (status === "complete" && amount !== null) return { kind: "point", label: currency(amount), qualification: "Estimated from reported token usage and published model pricing. This may differ from the amount charged by your provider." };
  if (standard !== null && fast !== null) return { kind: "range", label: `${currency(Math.min(standard, fast))}–${currency(Math.max(standard, fast))}`, qualification: "Exact model variant was not verified. This range represents supported pricing scenarios, not an actual billed charge or bounds on your bill." };
  const subtotal = get("nm5.estimated_subtotal_usd", record.estimated_subtotal_usd) ?? (status === "partial" ? amount : null);
  if (subtotal !== null) return { kind: "partial", label: `${currency(subtotal)} partial`, qualification: "Partial estimated subtotal for priced usage only. Unpriced usage is excluded; this is not a complete run estimate or a verified minimum." };
  const scenario = standard ?? fast;
  if (scenario !== null) return { kind: "partial", label: `${currency(scenario)} scenario`, qualification: `Only the ${standard !== null ? "Standard" : "Fast"} pricing scenario is available. The exact variant is unverified. This is not an actual billed charge.` };
  return { kind: "unavailable", label: "Not available", qualification: "Reported usage and supported pricing do not provide a defensible estimate." };
}
export function spanTiming(span: Span): { label: string; explanation: string } {
  const a = span.attributes;
  const provenance = a["nm5.timing_source"] ?? a["nm5.duration_source"];
  if (provenance === "inferred") return { label: `${formatMs(span.duration_ms)} inferred`, explanation: "Duration inferred from available events; not a precise measured execution duration." };
  if (provenance === "completion_only" || (span.duration_ms === 0 && (span.type === "agent" || a["nm5.span_source"] === "hooks" || a["nm5.usage_event"]))) return { label: "Not reported", explanation: "No measured execution duration is available. A completion-only observation or synthetic parent may contain a zero placeholder; it is not a measured 0 ms operation." };
  if (span.type === "agent" && !span.parent_span_id) return { label: `${formatMs(span.duration_ms)} elapsed`, explanation: "Parent session elapsed window reconstructed from lifecycle observations, including idle time; not precise active execution duration." };
  return { label: formatMs(span.duration_ms), explanation: provenance ? `Timing source: ${String(provenance)}.` : a["nm5.span_source"] === "hooks" ? "Reported hook duration. The stored record does not verify a separate start/end observation pair." : "Recorded span duration. Additional timing provenance, when recorded, is available in Attributes." };
}

const SOURCE_LABELS: Record<string, string> = {
  "codex-cli": "Codex CLI",
  "codex-exec": "Codex exec",
  "codex-ide": "Codex IDE",
  codex: "Codex",
  cli: "Codex CLI",
  cursor: "Cursor",
  vscode: "VS Code Copilot",
  "vscode-copilot": "VS Code Copilot",
  otel: "OpenTelemetry",
};

/** Human label for a run's reported source; unknown values are shown verbatim. */
export function sourceLabel(source: string | null | undefined, agentFramework?: string | null): string {
  const value = source ?? agentFramework ?? null;
  if (!value) return "Source not reported";
  return SOURCE_LABELS[value] ?? value;
}
