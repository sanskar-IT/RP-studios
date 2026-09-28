# Narrative Evaluation

The evaluation harness answers the milestone's central question with numbers
instead of impressions: can the engine maintain a consistent fictional world
for 50–100+ narrative turns while the operator intervenes, possesses
characters, branches timelines, and overrides canon?

## Running it

```text
python -m services.evaluation.harness --out reports/evaluation.json --print
```

Options:

```text
--scenario mystery|intrigue|combat|romance|canon   (repeatable; default is all five)
--turns 10|25|50|100                               (repeatable; default is all four)
--out reports/evaluation.json
--print                                            (human-readable summary to stdout)
```

No live model provider is involved. The scripted provider replays each
scenario's turn plan in index order, so the only thing being measured is the
engine: context assembly, memory, commitments, knowledge isolation, branching,
validation, and state projection. Exit code is 0 when every invariant holds and
1 otherwise.

## The five scenarios

| Key | Title | What it stresses |
| --- | --- | --- |
| `mystery` | The sealed library | Hidden information, character knowledge, suspicion vs certainty, a fact learned at turn 14 recalled at turn 57 |
| `intrigue` | The eastern garrison | A commitment that must survive 100 turns, relationships, deception, multiple actors |
| `combat` | The broken line | Injuries, death, location, possessions, rapidly changing state |
| `romance` | The evening before | Relationship state, emotional continuity, a long-term commitment |
| `canon` | The sealed harbour | Imported canon, user override, divergence, and a fork that must keep both answers |

Each scenario is data in `services/evaluation/scenarios.py`: characters, lore,
sources, commitments, a deterministic turn recipe, suspicion probes, and a list
of invariants drawn from a fixed vocabulary (`state_consistent`,
`no_knowledge_leak`, `memory_retrieval`, `commitment_persistence`,
`branch_correctness`, `lore_activation`, `context_bounded`, `event_validity`,
`no_transcript_as_memory`, `suspicion_not_certainty`). A scenario may not
declare an invariant kind the harness cannot evaluate; the module refuses to
import if one does.

## What is measured

Per scenario and turn count, the report records:

```text
Scenario: Detective Mystery
Turns: 100
State contradictions: 0
Knowledge leaks: 0
Broken commitments: 0
Invalid events: 0
Branch leaks: 0
Memory retrieval failures: 0
Rejected generations: 0
Context overflows: 0
Average context: 577 tokens
Peak context: 640 tokens
Memory rows per turn: 0.08
```

Plus per-turn samples (tokens, memories considered and retrieved, lore
activated and missed, events committed, validation errors, stage timings), the
failed-invariant list with findings that name the turn and the cause, and the
full invariant table. The schema is `narrative-evaluation/1`; the current
report is checked in at `reports/evaluation.json`.

Notable definitions:

- **Memory rows per turn** is the transcript test. The previous write policy
  produced about 5 rows per turn; anything near or below 1 means the transcript
  is no longer the memory store.
- **Knowledge leaks** are counted by the same distinctive-payload definition the
  engine uses, read against post-turn state — the strictest possible reading.
  Facts stated by the turn's own instruction are excluded as operator
  intervention, and a turn that kills its own actor is a death, not a violation.
- **Branch isolation** forks the finished timeline, diverges it, and checks that
  the parent is byte-identical before and after, that the branch keeps what it
  inherited and holds what it created, and that branch memories surface on the
  branch but never on the parent.
- **Stage timings** are wall-clock and therefore excluded from determinism
  comparisons; counters, averages, and verdicts are not.

## Determinism

Counters, averages, and invariant verdicts are identical between runs on the
same commit; only generated identifiers and wall-clock timings differ. The token
estimator is pinned to the heuristic fallback so a report does not change shape
because a tokenizer library happens to be installed. `tests/test_evaluation.py`
asserts all of this, including that the report is machine-readable and that two
consecutive runs agree.

## Lorebook load baseline

`tests/test_lore_load.py` establishes the under-load behaviour the audit found
missing: a 200-entry book with one weak shared keyword activates at most the
24-entry cap inside a fifth of the input budget, missed entries are reported in
`considered`, and the token estimate counts total words. The harness's
`lore_activation` invariant fails any turn that injects more than 12 entries at
once.
