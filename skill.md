# OpenCode Repository Skills

Place these under:

```text
.opencode/skills/
```

Each skill should be short, operational, and focused on rules the coding agent needs repeatedly.

---

## `.opencode/skills/project-governance/SKILL.md`

# Project Governance

This project is an AI Narrative Studio.

The PRD is the authoritative source for product behavior.

Do not redefine product behavior for implementation convenience.

Preserve these distinctions:

```text
Character definition ≠ character runtime state
Source material ≠ world state
Intent ≠ immediate action
Timeline ≠ transcript
Memory ≠ chat history
User authority ≠ AI autonomy
```

Do not implement destructive historical edits.

Prefer reversible decisions.

Document significant architectural changes through ADRs.

Do not add V2/V3 functionality to MVP unless necessary for the core architecture.

---

## `.opencode/skills/narrative-engine/SKILL.md`

# Narrative Engine

The narrative engine is the core of the product.

The AI performs narrative execution while the user controls intent.

Core flow:

```text
Input
→ Intent
→ Context
→ Stage
→ Approve
→ Select actor
→ Generate
→ Validate
→ Extract events
→ Commit
→ Project state
→ Update memory
→ Checkpoint
```

Do not implement the system as a simple “append message to chat history” loop.

Major narrative actions must become structured events.

The engine must support:

* AI autonomy;
* user direction;
* user narration;
* user character possession;
* world edits;
* intent commitments;
* canon overrides;
* branching.

---

## `.opencode/skills/state-and-memory/SKILL.md`

# State and Memory

State is more important than generated prose.

Separate:

```text
Permanent Memory
Persistent State
Scene Memory
Working Context
Archive
```

Never give a character information merely because the author/model knows it.

Character knowledge must be explicit.

Prefer event-sourced changes plus state projections.

Historical state must be reproducible.

When uncertain whether information should persist, classify it explicitly rather than silently storing it forever.

Do not inject all historical context into every model call.

---

## `.opencode/skills/director-system/SKILL.md`

# Director System

Director instructions represent user intent.

Examples:

```text
“Make the conversation more hostile.”
“Have the General betray the Emperor.”
“Kill the character suspiciously and make the rest panic.”
```

These should normally become structured intent.

Do not execute high-level instructions literally unless the user explicitly requests an immediate event.

When an instruction represents a desired future outcome, create a story commitment.

Allow the AI to construct a plausible path toward the intended outcome.

The user has final fictional authority within system constraints.

---

## `.opencode/skills/character-cards/SKILL.md`

# Character Card Compatibility

The product must preserve compatibility with the Chub/Tavern character-card ecosystem.

Support:

```text
Character Card V1
Character Card V2
JSON
PNG/APNG embedded card data
Character Books
Lorebooks
```

Do not discard unknown extension data.

Normalize external data into the internal character model.

Never overwrite source definition with runtime state.

Support alternate greetings where present.

Treat character books as structured lore, not arbitrary plain text.

Maintain fixture files for real-world compatibility cases.

---

## `.opencode/skills/provider-adapters/SKILL.md`

# Provider Adapters

The narrative engine must not depend on a specific model vendor.

The application targets:

```text
Local model endpoints
OpenAI-compatible endpoints
```

Provider adapters own:

* authentication;
* endpoint formatting;
* model configuration;
* streaming;
* structured generation;
* capability detection;
* errors and retries.

Core narrative logic must only interact with the provider abstraction.

Do not assume batching.

Do not encode provider-specific behavior in scene/state logic.

---

## `.opencode/skills/lorebook-engine/SKILL.md`

# Lorebook Engine

Lorebooks are structured retrieval systems.

Preserve:

* primary keys;
* secondary keys;
* scan depth;
* token budget;
* insertion order;
* enabled state;
* recursive scanning;
* constant entries;
* probability where supported.

Activation must be inspectable.

Provide debugging information showing why an entry was or was not activated.

Character books and world/project books must remain distinct.

---

## `.opencode/skills/timeline-branching/SKILL.md`

# Timeline Branching

History is immutable.

Any edit to an earlier historical point creates a branch.

Never rewrite previous events in place.

Branches must preserve:

```text
parent checkpoint
event sequence
state snapshot
new divergent events
```

A branch is a first-class timeline entity.

Original history must remain reproducible.

---

## `.opencode/skills/testing/SKILL.md`

# Testing Rules

Narrative prose is nondeterministic.

Core state behavior must not be.

Use mocked model outputs for deterministic tests.

Prioritize:

```text
Character import
Lorebook import
Lore activation
State projection
Character knowledge boundaries
Intent commitments
Canon overrides
Timeline branching
Provider adapters
User possession
Memory retrieval
```

Every historical mutation feature must include branch-preservation tests.

Every importer must include malformed-input tests.

Do not rely solely on snapshot tests of generated prose.

---

## `.opencode/skills/frontend-studio/SKILL.md`

# Studio UI

The application is desktop-first.

The interface should feel like a creative studio, not a messenger app.

Primary workspace:

```text
Cast
Scene Performance
State
Director/Actor Input
Timeline Controls
```

Critical narrative operations must remain obvious:

```text
Stage
Continue
Direct
Possess
Regenerate
Fork
Inspect
```

Do not bury state or branching controls in settings.

Prefer progressive disclosure over a permanently crowded interface.

---

## `.opencode/skills/security/SKILL.md`

# Security

This is a self-hosted BYOK application.

API credentials must not appear in:

* prompts;
* generation logs;
* story exports;
* client-visible application state;
* error messages.

Do not bypass provider safety mechanisms.

Never weaken safety constraints because the user has fictional authority.

Protect local configuration and session data appropriately for a self-hosted single-user application.

---

# Skill Interaction Order

When implementing a narrative feature, consult skills conceptually in this order:

```text
project-governance
      ↓
narrative-engine
      ↓
director-system
      ↓
state-and-memory
      ↓
timeline-branching
      ↓
provider-adapters
      ↓
frontend-studio
      ↓
testing
```

When handling imported RP assets:

```text
project-governance
      ↓
character-cards
      ↓
lorebook-engine
      ↓
state-and-memory
      ↓
testing
```

When modifying infrastructure:

```text
project-governance
      ↓
provider-adapters / security / database rules
      ↓
testing
```
