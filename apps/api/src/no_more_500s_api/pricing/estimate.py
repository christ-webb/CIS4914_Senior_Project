from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from no_more_500s_api.domain.models import Span, SpanType
from no_more_500s_api.pricing.lookup import PricingEntry, lookup_pricing

MILLION = Decimal("1000000")
STANDARD_GROK_47_RATES = (
    Decimal("2.00"),
    Decimal("0.50"),
    Decimal("6.00"),
)


@dataclass
class GenerationEstimate:
    status: str
    amount: Decimal | None = None
    reasons: list[str] = field(default_factory=list)
    pricing_id: str | None = None
    registry_version: str | None = None
    source_url: str | None = None
    verified_date: str | None = None
    effective_date: str | None = None
    estimate_basis: str = "current_rate"
    unpriced_categories: list[str] = field(default_factory=list)
    scenario: bool = False
    variant: str | None = None


def _int_attr(attributes: dict[str, Any], key: str) -> int | None:
    value = attributes.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0:
        return int(value)
    return None


def _str_attr(attributes: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _usage_generation(span: Span) -> bool:
    return span.type is SpanType.LLM and bool(span.attributes.get("nm5.generation_id"))


def _category_cost(tokens: int | None, rate: Decimal | None) -> Decimal | None:
    if tokens is None or rate is None:
        return None
    return (Decimal(tokens) * rate) / MILLION


def _unavailable(*reasons: str, variant: str | None = None) -> GenerationEstimate:
    return GenerationEstimate(status="unavailable", reasons=list(reasons), variant=variant)


def _apply_surcharge(
    entry: PricingEntry, input_tokens: int | None
) -> tuple[Decimal, Decimal | None, Decimal]:
    input_rate = entry.input_usd_per_million
    cache_rate = entry.cache_read_usd_per_million
    output_rate = entry.output_usd_per_million
    if input_tokens is None:
        return input_rate, cache_rate, output_rate
    for rule in entry.input_surcharges:
        threshold = int(rule.get("when_input_tokens_gt") or 0)
        if input_tokens <= threshold:
            continue
        if rule.get("standard_multiplier") is not None:
            factor = Decimal(str(rule["standard_multiplier"]))
            input_rate = STANDARD_GROK_47_RATES[0] * factor
            cache_rate = STANDARD_GROK_47_RATES[1] * factor
            output_rate = STANDARD_GROK_47_RATES[2] * factor
        elif rule.get("multiplier") is not None:
            factor = Decimal(str(rule["multiplier"]))
            input_rate *= factor
            if cache_rate is not None:
                cache_rate *= factor
            output_rate *= factor
    return input_rate, cache_rate, output_rate


def estimate_generation(
    span: Span,
    *,
    variant_override: str | None = None,
    scenario: bool = False,
) -> GenerationEstimate:
    attrs = span.attributes
    if attrs.get("nm5.token_status") != "reported":
        return GenerationEstimate(status="unavailable", reasons=["usage_not_reported"])
    accounting = str(attrs.get("nm5.token_accounting") or "raw")
    if accounting == "inconsistent" or attrs.get("nm5.usage_conflict"):
        return GenerationEstimate(status="unavailable", reasons=["inconsistent_or_ambiguous_usage"])
    if str(attrs.get("nm5.billable_breakdown") or "") != "complete":
        input_tokens = _int_attr(attrs, "gen_ai.usage.input_tokens")
        output_tokens = _int_attr(attrs, "gen_ai.usage.output_tokens")
        if input_tokens is None or output_tokens is None:
            return _unavailable("incomplete_billable_categories")

    input_tokens = _int_attr(attrs, "gen_ai.usage.input_tokens")
    output_tokens = _int_attr(attrs, "gen_ai.usage.output_tokens")
    cache_read = _int_attr(attrs, "gen_ai.usage.cache_read_tokens")
    cache_write = _int_attr(attrs, "gen_ai.usage.cache_write_tokens")
    uncached = _int_attr(attrs, "gen_ai.usage.uncached_input_tokens")
    if input_tokens is None or output_tokens is None:
        return _unavailable("incomplete_billable_categories")

    model_id = _str_attr(attrs, "nm5.model_id")
    model_display = _str_attr(attrs, "gen_ai.request.model")
    variant = variant_override or _str_attr(attrs, "nm5.model_variant")
    if not variant:
        return GenerationEstimate(
            status="unavailable",
            reasons=["unknown_model_variant"],
            variant=None,
        )

    entry = lookup_pricing(model_id=model_id, model_display=model_display, variant=variant)
    if entry is None:
        return _unavailable("unknown_model_pricing", variant=variant)

    input_rate, cache_rate, output_rate = _apply_surcharge(entry, input_tokens)
    priced = Decimal("0")
    unpriced: list[str] = []
    reasons: list[str] = []

    if accounting == "inclusive_cache":
        if uncached is None:
            cache_total = (cache_read or 0) + (cache_write or 0)
            uncached = input_tokens - cache_total
        input_cost = _category_cost(uncached, input_rate)
        cache_cost = _category_cost(cache_read, cache_rate) if cache_read else Decimal("0")
        output_cost = _category_cost(output_tokens, output_rate)
        if input_cost is None or cache_cost is None or output_cost is None:
            return _unavailable("missing_priced_rate", variant=variant)
        priced = input_cost + cache_cost + output_cost
        if cache_write:
            if entry.cache_write_covered_by == "input_when_inclusive_cache":
                reasons.append("cache_write_covered_by_inclusive_input")
            elif entry.cache_write_usd_per_million is None:
                unpriced.append("cache_write")
                reasons.append("unpublished_cache_write_rate")
            else:
                write_cost = _category_cost(cache_write, entry.cache_write_usd_per_million)
                if write_cost is None:
                    unpriced.append("cache_write")
                else:
                    priced += write_cost
    else:
        input_cost = _category_cost(input_tokens, input_rate)
        output_cost = _category_cost(output_tokens, output_rate)
        cache_cost = Decimal("0")
        if cache_read:
            if cache_rate is None:
                unpriced.append("cache_read")
                reasons.append("unpublished_cache_read_rate")
            else:
                cache_cost = _category_cost(cache_read, cache_rate) or Decimal("0")
        write_cost = Decimal("0")
        if cache_write:
            if entry.cache_write_usd_per_million is None:
                unpriced.append("cache_write")
                reasons.append("unpublished_exclusive_cache_write_rate")
            else:
                write_cost = (
                    _category_cost(cache_write, entry.cache_write_usd_per_million) or Decimal("0")
                )
        if input_cost is None or output_cost is None:
            return _unavailable("missing_priced_rate", variant=variant)
        priced = input_cost + output_cost + cache_cost + write_cost

    status = "partial" if unpriced else "complete"
    if scenario:
        reasons.append("variant_scenario")
    return GenerationEstimate(
        status=status,
        amount=priced,
        reasons=reasons,
        pricing_id=entry.pricing_id,
        registry_version=entry.registry_version,
        source_url=entry.source_url,
        verified_date=entry.verified_date,
        effective_date=entry.effective_date,
        estimate_basis="current_rate" if entry.effective_date is None else "effective_date",
        unpriced_categories=unpriced,
        scenario=scenario,
        variant=variant,
    )


def estimate_trace_usage(spans: list[Span]) -> dict[str, Any]:
    generations = [span for span in spans if _usage_generation(span)]
    reported = [
        span
        for span in generations
        if span.attributes.get("nm5.token_status") == "reported"
    ]
    estimates = [estimate_generation(span) for span in reported]
    complete = [item for item in estimates if item.status == "complete" and item.amount is not None]
    partial = [item for item in estimates if item.status == "partial" and item.amount is not None]
    covered = complete
    subtotal_items = complete + partial
    total_generations = len(reported)
    covered_count = len(covered)
    amounts = [item.amount for item in subtotal_items if item.amount is not None]
    subtotal = sum(amounts, Decimal("0")) if amounts else None
    full_status = "unavailable"
    full_amount: Decimal | None = None
    if total_generations == 0:
        full_status = "unavailable"
    elif covered_count == total_generations and not partial:
        full_status = "complete"
        full_amount = subtotal
    elif subtotal_items:
        full_status = "unavailable"

    scenarios: dict[str, str] | None = None
    unknown_variant = any("unknown_model_variant" in item.reasons for item in estimates)
    if unknown_variant and reported:
        scenario_totals: dict[str, Decimal] = {}
        scenario_ok = True
        for variant in ("standard", "fast"):
            running = Decimal("0")
            for span in reported:
                item = estimate_generation(span, variant_override=variant, scenario=True)
                if item.amount is None:
                    scenario_ok = False
                    break
                running += item.amount
            scenario_totals[variant] = running
        if scenario_ok:
            scenarios = {name: str(value) for name, value in scenario_totals.items()}

    versions = sorted({item.registry_version for item in subtotal_items if item.registry_version})
    pricing_ids = sorted({item.pricing_id for item in subtotal_items if item.pricing_id})
    first = next((item for item in covered or subtotal_items), None)
    if first is None and reported:
        sample = estimate_generation(
            reported[0], variant_override="standard", scenario=True
        )
        if sample.source_url:
            first = sample
            if sample.registry_version:
                versions = sorted({*versions, sample.registry_version})
    parent_only = any(span.attributes.get("nm5.usage_scope") == "parent_agent" for span in reported)
    return {
        "nm5.estimated_cost_status": full_status,
        "nm5.estimated_cost_usd": str(full_amount) if full_amount is not None else None,
        "nm5.estimated_subtotal_usd": (
            str(subtotal) if subtotal is not None and full_status != "complete" else None
        ),
        "nm5.estimated_subtotal_status": (
            "partial" if subtotal is not None and full_status != "complete" else full_status
        ),
        "nm5.estimate_covered_generations": covered_count,
        "nm5.estimate_total_generations": total_generations,
        "nm5.estimate_basis": first.estimate_basis if first else "current_rate",
        "nm5.pricing_registry_version": ",".join(versions) if versions else None,
        "nm5.pricing_id": ",".join(pricing_ids) if pricing_ids else None,
        "nm5.pricing_source": first.source_url if first else None,
        "nm5.pricing_verified_date": first.verified_date if first else None,
        "nm5.pricing_effective_date": (
            first.effective_date if first and first.effective_date else None
        ),
        "nm5.estimate_mixed_pricing_versions": len(versions) > 1,
        "nm5.scenario_standard_usd": scenarios.get("standard") if scenarios else None,
        "nm5.scenario_fast_usd": scenarios.get("fast") if scenarios else None,
        "nm5.estimated_cost_scope": "parent_agent" if parent_only else None,
    }


def annotate_span_estimate(span: Span) -> Span:
    if not _usage_generation(span):
        return span
    estimate = estimate_generation(span)
    updates = dict(span.attributes)
    updates["nm5.estimated_cost_status"] = estimate.status
    if estimate.amount is not None and not estimate.scenario:
        updates["nm5.estimated_cost_usd"] = str(estimate.amount)
    if estimate.pricing_id:
        updates["nm5.pricing_id"] = estimate.pricing_id
    if estimate.reasons:
        updates["nm5.estimate_reasons"] = ",".join(estimate.reasons)
    if estimate.unpriced_categories:
        updates["nm5.unpriced_categories"] = ",".join(estimate.unpriced_categories)
    if estimate.estimate_basis:
        updates["nm5.estimate_basis"] = estimate.estimate_basis
    return span.model_copy(update={"attributes": updates})
