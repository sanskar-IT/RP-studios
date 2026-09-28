# ADR 0002: Provider Boundary

## Status

Accepted.

## Decision

All model calls cross a small provider protocol. The narrative pipeline depends on that protocol rather than an OpenAI client or a vendor-specific request object.

## Consequences

A heuristic provider can run the product offline and make tests deterministic. Compatible local and hosted endpoints can be added without changing state or event logic. Provider-specific capabilities remain adapter concerns.
