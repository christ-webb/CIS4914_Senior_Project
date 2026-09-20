# ADR 0002: Use ClickHouse for MVP persistence

## Status

Accepted, implementation pending

## Context

Agent traces are append-heavy event data queried by project, time, status,
latency, tokens, and cost. The proposal allowed ClickHouse or TimescaleDB.

## Decision

Use ClickHouse for the semester MVP and avoid maintaining two storage paths.
Develop first against a repository interface and an in-memory implementation.

## Consequences

- The database matches analytical trace queries and the approved stack.
- The team has one database to learn and operate.
- Authentication or other transactional product data may need a different
  store later.

