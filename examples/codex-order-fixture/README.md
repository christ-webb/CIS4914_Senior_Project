# Order-processing fixture

Disposable fixture for No More 500s agent-integration tests. Two tests fail on
purpose (`test_total_with_discount`, `test_count_units`). Copy this directory
to a scratch location before pointing an agent at it; never edit it in place.

Run: `python3 -m unittest -q`
