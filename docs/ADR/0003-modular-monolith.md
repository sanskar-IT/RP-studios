# ADR 0003: Modular Monolith

## Status

Accepted.

## Decision

The MVP is a single deployable API and web application with explicit service modules. PostgreSQL is the system of record. No Redis, queue, or distributed worker is introduced without a concrete scaling or reliability requirement.

## Consequences

The first implementation stays easy to run self-hosted and keeps narrative transactions local. Service boundaries remain explicit so a later worker or search index can be introduced without collapsing the domain model into a chat transcript.
