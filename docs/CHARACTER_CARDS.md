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

### Compressed metadata is bounded on output, not just input

`zTXt` and `iTXt` chunks with the compression flag are inflated during parsing, and
that inflation is entirely attacker-controlled: a PNG chunk's length, flag, and
payload are all supplied by the upload. `MAX_IMPORT_BYTES` bounds the uploaded file
and therefore cannot bound this — the bytes that matter are the ones it expands to.

`zlib.decompress` allocates whatever the stream claims. Measured on the original
implementation: 4.1 KiB of input reached 13 MiB peak, 16 KiB reached 52 MiB.

`_bounded_inflate` feeds the stream with a `max_length` ceiling of 4 MiB, so a
bomb is abandoned at the overshoot rather than after being expanded. The ceiling
sits well above `MAX_TEXT_LENGTH` (200 000 characters), which is the limit the
imported text is then held to, so a real card is never caught by it.

The buffer fed to each call is `decompressor.unconsumed_tail`, not a fresh slice
of the input. When `max_length` is reached, zlib retains the input it could not
use there and stops; that tail must be fed back before the stream advances. An
implementation that slices the original buffer instead leaves the tail
unprocessed, `eof` is never reached, and valid data is refused as truncated.
Repetitive content spans the most steps and so hits this hardest — 1 MiB of
repeated bytes, compressing to about 1 KiB, spans sixteen 64 KiB output steps and
was rejected as corrupt before the loop was corrected.

Both call sites keep their own error idiom: a rejected `iTXt` chunk is skipped, a
rejected `zTXt` chunk sets its text to `None`, and in both cases a lower-priority
valid chunk is still used.

Truncated compressed data is refused as well. `decompressobj` does not raise on an
incomplete stream — it returns whatever decompressed before the cut, which for a
card payload can still be valid JSON. Without the `eof` check, a half-written card
imported as a real one, silently and permanently.

`test_a_compression_bomb_is_rejected_without_being_materialised` builds a 64 MiB
payload that compresses to under 256 KiB and asserts it is refused. Alongside it,
`test_bounded_inflate_round_trips_highly_compressible_data` (1, 3, and 4 MB of
repeated bytes) and `test_bounded_inflate_accepts_exactly_the_ceiling_and_refuses_one_byte_over`
pin the boundary in both directions — a ceiling that cannot be hit is not a
ceiling, and one that cannot be passed safely is a rejecter.

## Fixtures and tests

`tests/fixtures/character_v1.json`, `character_v2.json`, `character_v1.png`, `character_v2.png`, and `character_v2.apng` cover legacy fields, optional fields, alternate greetings, embedded books, arbitrary extensions, PNG precedence, compressed metadata, and APNG detection. `lorebook_tavern.json`, `lorebook_array.json`, `lorebook_uid_keyed.json`, and `lorebook_malformed_optional.json` cover real-world book shapes and safe optional-field handling. Tests also cover round-trip normalization, bounded imports, lore activation, and API previews.
