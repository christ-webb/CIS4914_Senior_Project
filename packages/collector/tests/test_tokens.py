from nomore500s_collector.tokens import normalize_token_count


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
