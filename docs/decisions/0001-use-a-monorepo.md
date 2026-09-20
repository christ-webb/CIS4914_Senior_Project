# ADR 0001: Use a monorepo

## Status

Accepted

## Context

The four-person team must evolve an SDK, API, contracts, dashboard, examples,
and infrastructure together during a 16-week project.

## Decision

Keep these components in one repository with explicit package boundaries.

## Consequences

- Cross-component contract changes are reviewable in one pull request.
- One CI surface can validate the complete vertical slice.
- The team must preserve boundaries and avoid importing application internals
  across packages.

