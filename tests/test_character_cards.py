from __future__ import annotations

import base64
import json
import struct
import zlib

import pytest

from services.importers.character_cards import CardImportError, import_character_card


def test_v1_fixture_is_normalized_and_unknown_fields_survive():
    with open("tests/fixtures/character_v1.json", encoding="utf-8") as handle:
        result = import_character_card(json.load(handle))
    assert result.name == "Ilyra"
    assert result.card_version == "1"
    assert result.extensions["custom_fixture_field"] == {"kept": True}


def test_v2_fixture_preserves_book_and_arbitrary_extensions():
    with open("tests/fixtures/character_v2.json", encoding="utf-8") as handle:
        result = import_character_card(json.load(handle))
    assert result.name == "Mara"
    assert result.definition["alternate_greetings"] == ["The archive is already awake."]
    assert result.extensions["custom_v2_extension"] == ["preserve", "this"]
    assert result.character_book is not None
    assert result.character_book["entries"][0]["extensions"] == {"fixture": "preserved"}


def test_v2_round_trip_keeps_normalized_content():
    with open("tests/fixtures/character_v2.json", encoding="utf-8") as handle:
        result = import_character_card(json.load(handle))
    exported = result.to_v2()
    assert exported["spec"] == "chara_card_v2"
    assert exported["data"]["name"] == "Mara"
    assert exported["data"]["character_book"]["entries"][1]["content"].startswith("The index")
    assert exported["data"]["custom_v2_extension"] == ["preserve", "this"]


def test_png_embedded_card_is_imported():
    card = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {"name": "Embedded", "description": "From a PNG."},
    }
    encoded = base64.b64encode(json.dumps(card).encode())
    chunk = b"tEXt" + b"chara\x00" + encoded
    crc = zlib.crc32(b"tEXt" + b"chara\x00" + encoded).to_bytes(4, "big")
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(b"chara\x00" + encoded)) + chunk + crc
    result = import_character_card(png, "embedded.png")
    assert result.name == "Embedded"
    assert result.source_format == "png"


@pytest.mark.parametrize("value", [b"not json", b"\x89PNG\r\n\x1a\n", {"data": {"description": "missing name"}}])
def test_malformed_cards_are_rejected(value):
    with pytest.raises(CardImportError):
        import_character_card(value)
