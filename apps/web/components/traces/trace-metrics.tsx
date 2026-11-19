import { Disclosure } from "@/components/ui/disclosure";
import { attribute, costPresentation, currency, numeric } from "@/lib/presentation";
import { formatBilledCostLabel, formatMs, formatTokenLabel } from "@/lib/stats";
import type { Span, Trace } from "@/lib/types";

export function TraceMetrics({ trace, onFailure }: { trace: Trace; onFailure?: () => void }) {
  const cost = costPresentation(trace);
  const attr = (key: string, fallback?: unknown) => attribute(trace, key, fallback);
  const tokens = formatTokenLabel(trace.total_tokens, attr("nm5.token_status", trace.token_status) as string);
  const measured = tokens !== "Not reported";
  const scope = attr("nm5.usage_scope", trace.usage_scope);
  const fields: [string, unknown][] = [
    ["Model", attr("nm5.model_id", trace.model)], ["Variant", attr("nm5.model_variant", trace.model_variant)],
    ["Pricing basis", attr("nm5.estimate_basis", trace.estimate_basis)],
    ["Price verification date", attr("nm5.pricing_verified_date")],
    ["Covered generations", attr("nm5.estimate_covered_generations", trace.covered_generations)],
    ["Reported generations", attr("nm5.estimate_total_generations", trace.total_generations)],
  ];
  const source = attr("nm5.pricing_source");
  return <section className="trace-metrics" aria-label="Trace metrics">
    <Disclosure label="Duration" value={formatMs(trace.duration_ms)}><p>Elapsed session observation window, including idle time. This is not the sum of measured operation durations.</p><p>Individual timing provenance is available in the span inspector.</p></Disclosure>
    <Disclosure label="Total tokens" value={measured ? new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(trace.total_tokens) : tokens}>
      <dl className="detail-list"><div><dt>Exact total</dt><dd>{tokens}</dd></div>{["input", "output", "cache_read", "cache_write"].map(key => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{numeric(attr(`nm5.usage.${key}_tokens`))?.toLocaleString() ?? "Not reported"}</dd></div>)}<div><dt>Usage scope</dt><dd>{scope === "parent_agent" ? "Parent agent only" : scope ? String(scope).replaceAll("_", " ") : "Not reported"}</dd></div></dl>
      <p>Cached reads may already be included in input tokens. They are not added to the cumulative total a second time.</p>
      {scope === "parent_agent" && <p>Subagent tokens were not reported.</p>}
      {attr("nm5.context_tokens") != null && <p>Context occupancy: {String(attr("nm5.context_tokens"))}{attr("nm5.context_window_size") != null ? ` / ${attr("nm5.context_window_size")}` : ""}. This is separate from cumulative usage.</p>}
    </Disclosure>
    <Disclosure label="Estimated cost" value={cost.label}>
      <p>{cost.qualification}</p><dl className="detail-list">{fields.filter(([,v]) => v != null).map(([label,value]) => <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}
      {["standard", "fast"].map(variant => { const value = numeric(attr(`nm5.scenario_${variant}_usd`, variant === "standard" ? trace.scenario_standard_usd : trace.scenario_fast_usd)); return value === null ? null : <div key={variant}><dt>{variant} scenario</dt><dd>{currency(value)}</dd></div>; })}
      <div><dt>Usage coverage</dt><dd>{scope === "parent_agent" ? "Parent agent only" : scope ? String(scope).replaceAll("_", " ") : "As reported"}</dd></div>
      <div><dt>Actual billed cost</dt><dd>{formatBilledCostLabel(trace.total_cost_usd, attr("nm5.cost_status", trace.cost_status) as string)}</dd></div></dl>
      {typeof source === "string" && /^https?:\/\//.test(source) && <a href={source} target="_blank" rel="noreferrer">Published pricing source</a>}
      <p>USD list pricing. Unreported subscription credits and discounts are not applied. Estimated cost never substitutes for a provider-reported bill.</p>
      <details><summary>Pricing and token components</summary>{trace.spans.filter(s => s.attributes["nm5.pricing_id"] || s.attributes["nm5.usage_event"] || s.attributes["nm5.pricing_details"]).map((s, index) => <PricingComponents key={s.span_id} span={s} index={index} />)}</details>
    </Disclosure>
    <div className="failure-metric"><span>Failed operations</span><strong>{trace.spans.filter(s => s.status === "error").length}</strong>{onFailure && <button className="text-button" onClick={onFailure}>Inspect failures</button>}</div>
  </section>;
}

function PricingComponents({ span, index }: { span: Span; index: number }) {
  const details = span.attributes["nm5.pricing_details"] as Record<string, { amount_usd: string; rates: Record<string, string | null>; unpriced_categories: string[]; reasons: string[]; source?: string; verified_date?: string }> | undefined;
  return <details className="pricing-component"><summary>Generation {index + 1} · {String(span.attributes["nm5.model_id"] ?? span.name)}</summary>
    <dl className="detail-list">{Object.entries(span.attributes).filter(([key]) => key.startsWith("gen_ai.usage.")).map(([key, value]) => <div key={key}><dt>{key.replace("gen_ai.usage.", "").replaceAll("_", " ")}</dt><dd>{typeof value === "number" ? value.toLocaleString() : String(value)}</dd></div>)}</dl>
    {details ? Object.entries(details).map(([name, item]) => <div key={name}><strong>{name === "verified" ? "Verified pricing variant" : `${name} scenario`} · {currency(Number(item.amount_usd))}</strong><dl className="detail-list">{Object.entries(item.rates).map(([key, value]) => <div key={key}><dt>{key.replace("_usd_per_million", "").replaceAll("_", " ")} / 1M tokens</dt><dd>{value === null ? "Not published" : currency(Number(value))}</dd></div>)}</dl>{item.source && /^https?:\/\//.test(item.source) && <p><a href={item.source} target="_blank" rel="noreferrer">Pricing source</a>{item.verified_date ? ` · verified ${item.verified_date}` : ""}</p>}{item.unpriced_categories.length > 0 && <p>Excluded: {item.unpriced_categories.join(", ")}</p>}{item.reasons.length > 0 && <p>{item.reasons.map(reason => reason.replaceAll("_", " ")).join("; ")}</p>}</div>) : <p>Rate details were not included in this stored response. See the pricing source and span attributes for the recorded basis.</p>}
  </details>;
}
