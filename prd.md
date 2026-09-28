# AI Narrative Studio PRD

AI Narrative Studio is a self-hosted, single-user narrative simulation studio. The user may act as an actor, director, narrator, writer, world authority, or a combination of those roles. The system executes the user's narrative intent while preserving explicit fictional authority and inspectable state.

The product is not a chat client with a prompt around a transcript. Performance text is presentation; structured events and projected state are authoritative.

## MVP outcomes

The MVP lets a user:

1. Create a project, world, timeline, and cast.
2. Import Character Card V1/V2, JSON, PNG/APNG embedded cards, character books, and lorebooks.
3. Stage an underspecified premise and edit its inferred assumptions before execution.
4. Continue a scene with an AI-selected actor.
5. Issue director intent without turning it into an immediate literal event.
6. Possess any scene participant temporarily and submit authoritative user actions.
7. Detect and record accepted canon divergence.
8. Commit append-only events, checkpoints, memories, and state projections.
9. Fork from an immutable timeline node without changing the original history.
10. Inspect state, memories, lore activation, and event history.
11. Use a local or generic OpenAI-compatible provider without storing credentials in project data.

## Required conceptual distinctions

- Character definition is not character runtime state.
- Source material is not current world state.
- Director intent is not an immediate event.
- Timeline is not transcript.
- Memory is not raw history.
- User authority does not remove the need to validate and commit consequences.

## Interaction model

The studio exposes one workspace with cast, performance, state inspection, timeline controls, and a mode-aware input surface. The supported modes are Actor, Director, Narrator, World, Retcon, and Auto. Branching and inspection remain visible rather than hidden behind settings.

## Quality gates

Narrative state behavior is tested with deterministic fixtures and mocked provider output. Prose may be nondeterministic; event commits, state reconstruction, lore activation, importer normalization, intent handling, and branch preservation may not be.

The detailed implementation contract is maintained in `docs/PRD.md`.
