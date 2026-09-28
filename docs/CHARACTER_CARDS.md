# Character Cards and Lorebooks

## Supported inputs

- Character Card V1 JSON
- Character Card V2 JSON
- PNG/APNG files with embedded `chara`, `ccv2`, or `character` text metadata
- Character books embedded in card data
- Standalone JSON lorebooks

PNG parsing walks `tEXt`, `iTXt`, and `zTXt` chunks, validates chunk checksums, prefers `ccv2` over `character` and `chara`, detects APNG `acTL` chunks, and decodes the embedded base64 JSON without an image rendering dependency. The PNG layer only extracts metadata. PNG, APNG, and JSON cards all pass through the same normalizer.

## Normalization

The importer produces a stable internal character definition containing description, personality, scenario, first message, example messages, alternate greetings, prompts, creator metadata, tags, and version. External format does not leak into runtime state.

Unknown top-level, nested, and book-entry extension fields are retained in extension storage. Character books are normalized into structured Lorebook and LorebookEntry records, preserving primary and secondary keys, selective matching, enabled state, constant flags, ordering and priority, position, probability, per-entry scan depth, recursive scanning, model filters, source IDs, and entry extensions. V2-only automation and regex fields are preserved as metadata and reported as warnings; they are not silently discarded.

## Round trip and source recovery

`ImportedCharacterCard.to_v2()` rebuilds a V2 envelope from the normalized definition and character book. Canonical fields always win over extension keys. The original decoded card remains in `CharacterCard.payload`, and `Source.extra_data` records the filename, source format, and import warnings. Standalone lorebook JSON is stored in the linked `Source` record, while normalized entries retain source IDs and unknown metadata.

## Failure behavior

Malformed JSON, missing names, oversized files, excessive JSON depth, too many book entries, invalid embedded metadata, and unsupported file types return a typed import error. Malformed individual book entries are skipped with warnings so valid entries remain importable. Standalone lorebooks accept Tavern-style top-level arrays and UID-keyed entry objects.

## Fixtures and tests

`tests/fixtures/character_v1.json`, `character_v2.json`, `character_v1.png`, `character_v2.png`, and `character_v2.apng` cover legacy fields, optional fields, alternate greetings, embedded books, arbitrary extensions, PNG precedence, compressed metadata, and APNG detection. `lorebook_tavern.json`, `lorebook_array.json`, `lorebook_uid_keyed.json`, and `lorebook_malformed_optional.json` cover real-world book shapes and safe optional-field handling. Tests also cover round-trip normalization, bounded imports, lore activation, and API previews.
