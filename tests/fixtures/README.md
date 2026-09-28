# Compatibility fixtures

These fixtures are synthetic assets written for this repository to exercise public Chub/Tavern-compatible container shapes. They do not contain third-party character text, artwork, or licensed source material.

- `character_v1.json`: flat legacy card with unknown metadata.
- `character_v2.json`: wrapped V2 card with alternate greetings, extensions, and an embedded character book.
- `character_v1.png`: V1 card embedded in a PNG `tEXt` chunk.
- `character_v2.png`: V2 card embedded in a PNG `ccv2` chunk.
- `character_v2.apng`: V2 card in an APNG container.
- `lorebook_tavern.json`: object book with Tavern-style `key`, `keysecondary`, ordering, position, and extensions.
- `lorebook_array.json`: top-level array export.
- `lorebook_uid_keyed.json`: UID-keyed entry object.
- `lorebook_malformed_optional.json`: invalid optional numbers that must fall back safely.
- `provider/`: deterministic structured generation, staging, actor, failure, and branching responses used by narrative engine tests.
