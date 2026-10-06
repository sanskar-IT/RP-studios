from __future__ import annotations

import base64
import json
import struct
import zlib
from pathlib import Path

import pytest

from services.importers.character_cards import (
    MAX_IMPORT_BYTES,
    CardImportError,
    extract_embedded_card,
    import_character_card,
    import_lorebook,
)
from services.lorebook.evaluator import LoreCandidate, evaluate_lore

FIXTURES = Path(__file__).parent / "fixtures"


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def card_png(card: dict, keyword: bytes = b"ccv2", extra: bytes = b"") -> bytes:
    encoded = base64.b64encode(json.dumps(card).encode())
    return b"\x89PNG\r\n\x1a\n" + chunk(b"tEXt", keyword + b"\x00" + encoded) + extra + chunk(b"IEND", b"")


def test_v1_png_and_apng_report_the_correct_version_and_format():
    v1 = import_character_card((FIXTURES / "character_v1.png").read_bytes(), "character_v1.png")
    v2 = import_character_card((FIXTURES / "character_v2.png").read_bytes(), "character_v2.png")
    apng = import_character_card((FIXTURES / "character_v2.apng").read_bytes(), "character_v2.apng")
    assert v1.card_version == "1"
    assert v1.source_format == "png"
    assert v2.card_version == "2"
    assert apng.source_format == "apng"


def test_png_chunk_precedence_prefers_ccv2_over_chara():
    v1 = {"name": "V1"}
    v2 = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {"name": "V2"}}
    v1_payload = base64.b64encode(json.dumps(v1).encode())
    v2_payload = base64.b64encode(json.dumps(v2).encode())
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"tEXt", b"ccv2\x00" + v2_payload)
        + chunk(b"tEXt", b"chara\x00" + v1_payload)
        + chunk(b"IEND", b"")
    )
    extracted, source_format = extract_embedded_card(png)
    assert source_format == "png"
    assert import_character_card(png).name == "V2"
    assert extracted["data"]["name"] == "V2"


def test_a_compression_bomb_is_rejected_without_being_materialised():
    """A few KB of PNG must not be able to allocate hundreds of MB.

    ``zlib.decompress`` has no size ceiling, so a card whose compressed text
    chunk expands to gigabytes exhausts memory inside the parser — before the
    importer's own byte limit, which only bounds the *uploaded* file. Measured on
    the original code: 4.1 KiB of input reached 13 MiB peak, 16 KiB reached
    52 MiB.

    The bound is on the decompressed text, and it is enforced incrementally, so
    a bomb is abandoned mid-stream rather than after being fully expanded.
    """
    bomb = zlib.compress(b"A" * (64 * 1024 * 1024))
    assert len(bomb) < 256 * 1024, "bomb fixture should be small to compress well"

    card = {"name": "Bomb"}
    for chunk_type, payload in (
        (b"zTXt", b"ccv2\x00\x00" + bomb),
        (b"iTXt", b"ccv2\x00\x01\x00\x00\x00" + bomb),
    ):
        bomb_only = b"\x89PNG\r\n\x1a\n" + chunk(chunk_type, payload) + chunk(b"IEND", b"")
        with pytest.raises(CardImportError):
            import_character_card(bomb_only, "bomb.png")

        # With a legitimate lower-priority chunk present, the bomb chunk is
        # skipped and the real card still imports.
        fallback = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(chunk_type, payload)
            + chunk(b"tEXt", b"chara\x00" + base64.b64encode(json.dumps(card).encode()))
            + chunk(b"IEND", b"")
        )
        assert import_character_card(fallback, "bomb.png").name == "Bomb"


def test_bounded_inflate_stops_at_the_ceiling_and_keeps_normal_text():
    from services.importers.character_cards import (
        MAX_DECOMPRESSED_TEXT_BYTES,
        _bounded_inflate,
    )

    assert _bounded_inflate(zlib.compress(b"hello")) == b"hello"
    with pytest.raises(ValueError):
        _bounded_inflate(zlib.compress(b"A" * (MAX_DECOMPRESSED_TEXT_BYTES + 1)))


def test_bounded_inflate_rejects_truncated_streams():
    from services.importers.character_cards import _bounded_inflate

    with pytest.raises(ValueError):
        _bounded_inflate(zlib.compress(b"hello")[:-4])


@pytest.mark.parametrize("size", [1, 3, 4])
def test_bounded_inflate_round_trips_highly_compressible_data(size):
    """A stream spanning many steps must survive, not be called truncated.

    ``max_length`` halts decompression partway and parks the unused input in
    ``unconsumed_tail``; that tail has to be fed back before the stream advances.
    An implementation that instead slices the original buffer leaves the tail
    unprocessed, ``eof`` is never reached, and valid data is rejected as
    truncated — which is how a 1 MiB payload compressing to 1 KiB was refused.
    Repetitive data hits this hardest because it spans the most steps.
    """
    from services.importers.character_cards import _bounded_inflate

    raw = b"A" * (size * 1024 * 1024)
    assert len(zlib.compress(raw)) < 8 * 1024, "should compress hard enough to span steps"
    assert _bounded_inflate(zlib.compress(raw)) == raw


def test_bounded_inflate_accepts_exactly_the_ceiling_and_refuses_one_byte_over():
    from services.importers.character_cards import (
        MAX_DECOMPRESSED_TEXT_BYTES,
        _bounded_inflate,
    )

    at_limit = b"B" * MAX_DECOMPRESSED_TEXT_BYTES
    assert _bounded_inflate(zlib.compress(at_limit)) == at_limit
    with pytest.raises(ValueError):
        _bounded_inflate(zlib.compress(b"B" * (MAX_DECOMPRESSED_TEXT_BYTES + 1)))


def test_bounded_inflate_handles_an_empty_payload():
    from services.importers.character_cards import _bounded_inflate

    assert _bounded_inflate(zlib.compress(b"")) == b""


def test_a_legitimate_large_compressed_card_still_imports():
    """The ceiling must not break real cards.

    A 200k-character description is well under the limit, and a character card
    that big is unusual but legal.
    """
    long_description = "The lantern gutters. " * 8_000
    card = {"name": "Verbose", "description": long_description}
    assert 100_000 < len(long_description) < 200_000
    png = card_png(card, extra=chunk(b"iTXt", b"note\x00\x01\x00\x00\x00" + zlib.compress(b"ok")))
    result = import_character_card(png, "verbose.png")
    assert result.name == "Verbose"


def test_ztext_and_itxt_metadata_are_extracted():
    card = {"name": "Compressed"}
    encoded = base64.b64encode(json.dumps(card).encode())
    compressed = zlib.compress(encoded)
    ztxt_png = b"\x89PNG\r\n\x1a\n" + chunk(b"zTXt", b"chara\x00\x00" + compressed) + chunk(b"IEND", b"")
    assert import_character_card(ztxt_png).name == "Compressed"
    itxt_payload = b"ccv2\x00\x00\x00" + b"en\x00translated\x00" + encoded
    itxt_png = b"\x89PNG\r\n\x1a\n" + chunk(b"iTXt", itxt_payload) + chunk(b"IEND", b"")
    assert import_character_card(itxt_png).name == "Compressed"


def test_extension_cannot_overwrite_canonical_v2_fields_on_round_trip():
    card = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {"name": "Canonical", "description": "Original", "extensions": {"name": "Hijack", "description": "Hijack"}},
    }
    exported = import_character_card(card).to_v2()
    assert exported["data"]["name"] == "Canonical"
    assert exported["data"]["description"] == "Original"


def test_import_limits_and_depth_fail_cleanly():
    with pytest.raises(CardImportError):
        import_character_card(b"x" * (MAX_IMPORT_BYTES + 1))
    with pytest.raises(CardImportError):
        import_character_card({"name": "x" * 201})
    deep: dict = {}
    cursor = deep
    for _ in range(40):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    with pytest.raises(CardImportError):
        import_character_card(deep | {"name": "Deep"})


def test_lorebook_real_world_shapes_are_normalized():
    tavern = import_lorebook((FIXTURES / "lorebook_tavern.json").read_bytes())
    uid_keyed = import_lorebook(json.loads((FIXTURES / "lorebook_uid_keyed.json").read_text()))
    array = import_lorebook(json.loads((FIXTURES / "lorebook_array.json").read_text()))
    assert len(tavern["entries"]) == 3
    assert tavern["entries"][0]["keys"] == ["Public Safety", "Public Safety Commission"]
    assert tavern["entries"][0]["source_id"] == 0
    assert tavern["entries"][0]["extensions"]["fixture_source"] == "tavern-like"
    assert len(uid_keyed["entries"]) == 2
    assert uid_keyed["warnings"]
    assert len(array["entries"]) == 2


def test_malformed_optional_book_values_use_safe_defaults():
    result = import_lorebook((FIXTURES / "lorebook_malformed_optional.json").read_text())
    assert result["entries"][0]["keys"] == ["archive"]
    assert result["entries"][0]["probability"] == 1.0
    assert result["entries"][0]["extensions"]["unknown_field"] == {"preserved": True}
    with pytest.raises(CardImportError):
        import_lorebook(b"\xff\xfe\x00")


def test_evaluator_is_deterministic_by_default_and_respects_scan_depth():
    chance = LoreCandidate(id="chance", name="Chance", content="A hidden detail.", primary_keys=["archive"], probability=0.5)
    first = evaluate_lore([chance], "archive")
    second = evaluate_lore([chance], "archive")
    assert [entry.entry_id for entry in first.activated] == [entry.entry_id for entry in second.activated]
    root = LoreCandidate(id="root", name="Root", content="The archive points to the index.", primary_keys=["archive"], scan_depth=1)
    child = LoreCandidate(id="child", name="Child", content="The index is a living map.", primary_keys=["index"], scan_depth=4)
    result = evaluate_lore([root, child], "archive", token_budget=100)
    assert {entry.entry_id for entry in result.activated} == {"root", "child"}
    assert result.considered.count({"entry_id": "child", "activated": True, "reasons": ["recursive from root", "primary key: index"]}) == 1
