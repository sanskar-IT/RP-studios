# Generation Tracing

Every generation answers the same question — what exactly happened, and why —
and the trace is the durable answer. It is written in the same transaction as
the generation row, so a failed or rejected turn is as inspectable as a
successful one.

## What is stored

```text
Generation ID
Timeline ID
Scene ID
Actor
Input mode
Provider
Model
Context components        per-component source, priority, tokens, included/excluded, reason
Retrieved memories        ids, classes, scopes, scores, and why others were dropped
Activated lore            entry ids, scopes, token costs, activation reasons
Active commitments        the planner context as the model saw it
Generated events          prose kept separate from claimed state changes
Validation results        accepted and rejected claims with reasons and recovery actions
Contradictions            structured warnings with accept / regenerate / correct_state
State changes             the committed events with their node ids
Memory writes             which seeds survived the lifecycle and why the rest did not
Performance               per-stage timings (context assembly, retrieval, lore,
                          provider request, validation, projection) plus the total
```

Stored on the row as `context_debug`, `validation`, and `trace` JSON columns,
all readable from a single `GET .../generations/{id}`. `GET
.../timelines/{id}/generations` lists recent generations with their totals,
validation status, and contradiction counts.

## What is never stored

Base URLs, API keys, headers, and any other provider credential. Only the
provider's name, the model identifier, and its declared capabilities are
recorded, because those are needed to explain a result and are not secret.
Credentials are configuration and stay in the environment. The trace builder
drops credential-shaped keys at any depth, and `tests/test_traces.py` pins
that behaviour — including that free prose mentioning the word "password" is
never mangled.

## Rejected and failed generations

- A **rejected** generation failed claim validation. Nothing committed; the
  trace records the reasons and the recovery actions (`retry`, `regenerate`,
  `edit`, `reject`). Retrying with a valid provider succeeds on the same scene.
- A **failed** generation never got a usable provider response. It still
  records the context that was assembled and the time each stage spent before
  failing, which is exactly when those numbers are interesting.
- A committed generation can still be disowned afterwards with
  `POST .../generations/{id}/reject`, which appends a `generation_rejected`
  event so the rejection is itself part of the history. Events are
  append-only; rejection marks, it does not erase.

## Reading a trace

The studio's Context panel renders the latest trace from the inspection
response and lets the operator open any recent generation: its context
composition, validation verdict, full trace JSON, and a reject button. For
scripted analysis, the evaluation harness reads the same rows directly and the
machine-readable report aggregates them.
