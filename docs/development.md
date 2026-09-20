# Development guide

## Branches and pull requests

Create a short-lived branch for each issue. Keep PRs scoped to one outcome and
include tests or explain why a test is not applicable. Do not commit directly
to `main` after branch protection is enabled.

## Commands

Run `make bootstrap` once, then `make check` before requesting review. Use
`make api-dev`, `make web-dev`, and `make demo` for the first vertical slice.

## Contract changes

Changing a trace or span field requires coordinated updates to:

1. JSON Schema in `packages/contracts`
2. SDK model and serialization test
3. API model and endpoint test
4. Frontend TypeScript type
5. ClickHouse schema or migration, once persistence is enabled

## Definition of done

- Behavior is covered by an automated check.
- Public contracts and setup instructions are current.
- No secrets or customer telemetry are committed.
- The end-to-end demo still works.

