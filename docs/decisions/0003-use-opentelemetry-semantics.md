# ADR 0003: Use OpenTelemetry semantics

## Status

Accepted

## Context

No More 500s should work across AI providers and agent frameworks without
inventing a proprietary instrumentation model for every integration.

## Decision

Use OpenTelemetry trace and span concepts plus namespaced generative-AI
attributes. The MVP HTTP payload mirrors these concepts while an OTLP exporter
is developed.

## Consequences

- The product can interoperate with common observability tooling.
- Custom agent attributes remain possible.
- The team must document any divergence from standard semantic conventions.

