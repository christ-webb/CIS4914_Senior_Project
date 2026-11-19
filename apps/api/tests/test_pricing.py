from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from no_more_500s_api.domain.models import Span, SpanStatus, SpanType, TraceCreate
from no_more_500s_api.pricing.estimate import estimate_generation, estimate_trace_usage
from no_more_500s_api.pricing.lookup import lookup_pricing, registry_version


def _llm(**attrs: object) -> Span:
    return Span(
        span_id=uuid4(),
        type=SpanType.LLM,
        name="turn",
        started_at=datetime(2026, 11, 18, tzinfo=UTC),
        duration_ms=10,
        status=SpanStatus.OK,
        token_count=int(attrs.get("token_count") or 0),
        attributes={
            "nm5.generation_id": str(attrs.get("generation_id") or "g1"),
            "nm5.token_status": "reported",
            "nm5.usage_scope": "parent_agent",
            **{
                key: value
                for key, value in attrs.items()
                if key not in {"token_count", "generation_id"}
            },
        },
    )


def test_lookup_requires_verified_variant() -> None:
    assert lookup_pricing(model_id="grok-4.7", variant=None) is None
    assert lookup_pricing(model_id="composer-2.5", variant="fast") is not None
    assert lookup_pricing(model_id="grok-4.7", variant="standard") is not None
    missing = lookup_pricing(
        model_id="grok-4.7", variant="standard", registry_version="missing"
    )
    assert missing is None


def test_geometry_like_unknown_variant_is_unavailable_with_scenarios() -> None:
    span = _llm(
        **{
            "nm5.model_id": "grok-4.7",
            "gen_ai.request.model": "grok-4.7",
            "nm5.token_accounting": "inclusive_cache",
            "nm5.billable_breakdown": "complete",
            "gen_ai.usage.input_tokens": 180819,
            "gen_ai.usage.output_tokens": 2830,
            "gen_ai.usage.cache_read_tokens": 137088,
            "gen_ai.usage.cache_write_tokens": 0,
            "gen_ai.usage.uncached_input_tokens": 43731,
            "token_count": 183649,
        }
    )
    estimate = estimate_generation(span)
    assert estimate.status == "unavailable"
    assert "unknown_model_variant" in estimate.reasons
    rolled = estimate_trace_usage([span])
    assert rolled["nm5.estimated_cost_status"] == "unavailable"
    assert rolled["nm5.estimated_cost_usd"] is None
    assert Decimal(rolled["nm5.scenario_standard_usd"]) == Decimal("0.172986")
    assert Decimal(rolled["nm5.scenario_fast_usd"]) == Decimal("0.345972")
    assert rolled["nm5.estimate_basis"] == "current_rate"
    assert rolled["nm5.pricing_effective_date"] is None
    assert rolled["nm5.pricing_registry_version"] is None or registry_version() in str(
        rolled.get("nm5.pricing_registry_version") or ""
    )


def test_verified_fast_and_standard_use_different_rates() -> None:
    attrs = {
        "nm5.model_id": "composer-2.5",
        "nm5.token_accounting": "inclusive_cache",
        "nm5.billable_breakdown": "complete",
        "gen_ai.usage.input_tokens": 20,
        "gen_ai.usage.output_tokens": 5,
        "gen_ai.usage.cache_read_tokens": 4,
        "gen_ai.usage.cache_write_tokens": 3,
        "gen_ai.usage.uncached_input_tokens": 13,
    }
    standard = estimate_generation(_llm(**attrs, **{"nm5.model_variant": "standard"}))
    fast = estimate_generation(_llm(**attrs, **{"nm5.model_variant": "fast"}))
    assert standard.status == "complete"
    assert fast.status == "complete"
    million = Decimal("1000000")
    expected = (
        Decimal("13") * Decimal("0.50") / million
        + Decimal("4") * Decimal("0.20") / million
        + Decimal("5") * Decimal("2.50") / million
    )
    assert standard.amount == expected
    assert fast.amount != standard.amount
    assert "cache_write_covered_by_inclusive_input" in standard.reasons


def test_exclusive_cache_write_without_rate_is_partial() -> None:
    estimate = estimate_generation(
        _llm(
            **{
                "nm5.model_id": "composer-2.5",
                "nm5.model_variant": "standard",
                "nm5.token_accounting": "raw",
                "nm5.billable_breakdown": "complete",
                "gen_ai.usage.input_tokens": 10,
                "gen_ai.usage.output_tokens": 2,
                "gen_ai.usage.cache_read_tokens": 4,
                "gen_ai.usage.cache_write_tokens": 3,
            }
        )
    )
    assert estimate.status == "partial"
    assert "cache_write" in estimate.unpriced_categories
    assert estimate.amount is not None


def test_inconsistent_usage_is_not_priced() -> None:
    estimate = estimate_generation(
        _llm(
            **{
                "nm5.model_id": "composer-2.5",
                "nm5.model_variant": "standard",
                "nm5.token_accounting": "inconsistent",
                "nm5.billable_breakdown": "unavailable",
                "gen_ai.usage.input_tokens": 2,
                "gen_ai.usage.output_tokens": 5,
                "gen_ai.usage.cache_read_tokens": 40,
            }
        )
    )
    assert estimate.status == "unavailable"


def test_missing_categories_are_not_zero() -> None:
    estimate = estimate_generation(
        _llm(
            **{
                "nm5.model_id": "composer-2.5",
                "nm5.model_variant": "standard",
                "nm5.token_accounting": "raw",
                "nm5.billable_breakdown": "partial",
                "gen_ai.usage.output_tokens": 9,
            }
        )
    )
    assert estimate.status == "unavailable"
    assert estimate.amount is None


def test_mixed_model_subtotal_does_not_claim_full_run() -> None:
    priced = _llm(
        generation_id="g1",
        **{
            "nm5.model_id": "composer-2.5",
            "nm5.model_variant": "standard",
            "nm5.token_accounting": "raw",
            "nm5.billable_breakdown": "complete",
            "gen_ai.usage.input_tokens": 10,
            "gen_ai.usage.output_tokens": 2,
        },
    )
    unknown = _llm(
        generation_id="g2",
        **{
            "nm5.model_id": "mystery-model",
            "nm5.model_variant": "standard",
            "nm5.token_accounting": "raw",
            "nm5.billable_breakdown": "complete",
            "gen_ai.usage.input_tokens": 10,
            "gen_ai.usage.output_tokens": 2,
        },
    )
    rolled = estimate_trace_usage([priced, unknown])
    assert rolled["nm5.estimated_cost_status"] == "unavailable"
    assert rolled["nm5.estimated_subtotal_usd"] is not None
    assert rolled["nm5.estimate_covered_generations"] == 1
    assert rolled["nm5.estimate_total_generations"] == 2


def test_legacy_sdk_totals_when_status_absent() -> None:
    trace = TraceCreate(
        project_id="p",
        name="sdk",
        spans=[
            Span(type=SpanType.LLM, name="call", duration_ms=1, token_count=4, cost_usd=0.02),
        ],
    )
    from no_more_500s_api.domain.models import Trace

    stored = Trace.from_create(trace)
    assert stored.total_tokens == 4
    assert stored.total_cost_usd == 0.02


def test_status_aware_from_create_skips_unavailable() -> None:
    from no_more_500s_api.domain.models import Trace

    stored = Trace.from_create(
        TraceCreate(
            project_id="p",
            name="mix",
            spans=[
                Span(
                    type=SpanType.LLM,
                    name="a",
                    duration_ms=1,
                    token_count=9,
                    attributes={"nm5.token_status": "unavailable"},
                ),
                Span(
                    type=SpanType.LLM,
                    name="b",
                    duration_ms=1,
                    token_count=3,
                    attributes={"nm5.token_status": "reported"},
                ),
            ],
        )
    )
    assert stored.total_tokens == 3


def test_pricing_disclosure_metadata_preserves_unknown_variant_status() -> None:
    from no_more_500s_api.pricing.estimate import annotate_span_estimate

    span = _llm(**{
        "nm5.model_id": "grok-4.7",
        "nm5.token_accounting": "inclusive_cache",
        "gen_ai.usage.input_tokens": 100,
        "gen_ai.usage.output_tokens": 10,
        "gen_ai.usage.cache_read_tokens": 50,
    })
    before = estimate_trace_usage([span])
    annotated = annotate_span_estimate(span)
    assert annotated.attributes["nm5.estimated_cost_status"] == "unavailable"
    assert "nm5.estimated_cost_usd" not in annotated.attributes
    details = annotated.attributes["nm5.pricing_details"]
    assert set(details) == {"standard", "fast"}
    for variant, detail in details.items():
        estimate = estimate_generation(span, variant_override=variant, scenario=True)
        assert Decimal(detail["amount_usd"]) == estimate.amount
        assert detail["rates"] == estimate.rates
        assert detail["source"] == estimate.source_url
    assert estimate_trace_usage([annotated]) == before
