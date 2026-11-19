from nomore500s_collector.tokens import (
    normalize_token_count,
    parse_token_usage,
    verified_model_variant,
)


def test_prefers_components_over_total() -> None:
    count, status = normalize_token_count(
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 999, "reasoning": 3}
    )
    assert count == 15
    assert status == "reported"


def test_zero_usage_is_reported() -> None:
    count, status = normalize_token_count({"input_tokens": 0, "output_tokens": 0})
    assert count == 0
    assert status == "reported"


def test_missing_usage_is_unavailable() -> None:
    assert normalize_token_count(None) == (None, "unavailable")
    assert normalize_token_count({}) == (None, "unavailable")


def test_parse_preserves_partial_categories() -> None:
    parsed = parse_token_usage({"output_tokens": 12})
    assert parsed.status == "reported"
    assert parsed.output_tokens == 12
    assert parsed.input_tokens is None
    assert parsed.total_tokens == 12
    assert parsed.accounting == "raw"
    assert parsed.total_completeness == "complete"
    assert parsed.billable_breakdown == "partial"


def test_parse_does_not_assume_inclusive_cache() -> None:
    parsed = parse_token_usage(
        {
            "input_tokens": 20,
            "output_tokens": 5,
            "cache_read_tokens": 4,
            "cache_write_tokens": 3,
        }
    )
    assert parsed.accounting == "inclusive_cache"
    assert parsed.uncached_input_tokens == 13
    assert parsed.input_tokens == 20
    assert parsed.total_tokens == 25
    assert parsed.billable_breakdown == "complete"


def test_parse_marks_inconsistent_cache_without_clamping() -> None:
    parsed = parse_token_usage(
        {
            "input_tokens": 2,
            "output_tokens": 5,
            "cache_read_tokens": 40,
            "cache_write_tokens": 10,
        }
    )
    assert parsed.accounting == "inconsistent"
    assert parsed.uncached_input_tokens is None
    assert parsed.input_tokens == 2
    assert parsed.cache_read_tokens == 40
    assert parsed.total_tokens == 7
    assert parsed.billable_breakdown == "unavailable"


def test_total_only_is_complete_total_without_billable_breakdown() -> None:
    parsed = parse_token_usage({"total_tokens": 40})
    assert parsed.total_completeness == "complete"
    assert parsed.billable_breakdown == "unavailable"


def test_variant_requires_explicit_fast_or_standard() -> None:
    assert verified_model_variant("composer-2.5") is None
    assert verified_model_variant("grok-4.7") is None
    assert verified_model_variant("cursor-grok-4.6-high") is None
    assert verified_model_variant("Composer 2.5 (Fast)") == "fast"
    assert verified_model_variant("composer-2.5-fast") == "fast"
    assert verified_model_variant("Composer 2.5 Standard") == "standard"
