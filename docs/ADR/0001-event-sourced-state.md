# ADR 0001: Append-Oriented Narrative State

## Status

Accepted.

## Decision

Narrative changes are represented as immutable events on timelines. A node stores a complete checkpoint of projected state for fast loading. Branches copy a checkpoint reference into a new timeline and append new events.

## Consequences

Historical state is reproducible and edits are non-destructive. State projection and checkpoint writes must be deterministic. Character state rows are derived conveniences, not the sole source of truth.
