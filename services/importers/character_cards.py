from __future__ import annotations

import base64
import binascii
import json
import zlib
from dataclasses import dataclass, field
from typing import Any

MAX_IMPORT_BYTES = 8 * 1024 * 1024
MAX_BOOK_ENTRIES = 10_000
MAX_NAME_LENGTH = 200
MAX_ENTRY_NAME_LENGTH = 300
MAX_TEXT_LENGTH = 200_000
MAX_JSON_DEPTH = 32


class CardImportError(ValueError):
    pass


@dataclass
class ImportedCharacterCard:
    name: str
    definition: dict[str, Any]
    extensions: dict[str, Any] = field(default_factory=dict)
    character_book: dict[str, Any] | None = None
    card_version: str = "2"
    source_format: str = "json"
    source_filename: str = ""
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_v2(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "description": self.definition.get("description", ""),
            "personality": self.definition.get("personality", ""),
            "scenario": self.definition.get("scenario", ""),
            "first_mes": self.definition.get("first_mes", ""),
            "mes_example": self.definition.get("mes_example", ""),
            "alternate_greetings": self.definition.get("alternate_greetings", []),
            "system_prompt": self.definition.get("system_prompt", ""),
            "post_history_instructions": self.definition.get("post_history_instructions", ""),
            "creator_notes": self.definition.get("creator_notes", ""),
            "tags": self.definition.get("tags", []),
            "creator": self.definition.get("creator", ""),
            "character_version": self.definition.get("character_version", ""),
            "character_book": self.character_book,
        }
        for key, value in self.extensions.items():
            if key not in data:
                data[key] = value
        return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}


KNOWN_FIELDS = {
    "spec",
    "spec_version",
    "data",
    "name",
    "description",
    "personality",
    "scenario",
    "first_mes",
    "first_message",
    "mes_example",
    "example_messages",
    "alternate_greetings",
    "alternate_mes",
    "system_prompt",
    "post_history_instructions",
    "creator_notes",
    "tags",
    "creator",
    "character_version",
    "character_book",
    "extensions",
}

ENTRY_FIELDS = {
    "id",
    "keys",
    "primary_keys",
    "secondary_keys",
    "comment",
    "name",
    "content",
    "constant",
    "selective",
    "enabled",
    "insertion_order",
    "order",
    "priority",
    "position",
    "probability",
    "scan_depth",
    "extensions",
    "recursive",
    "recursive_scanning",
    "enabled_for_model",
}


def _validate_json_limits(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise CardImportError("Imported JSON nesting exceeds the supported depth")
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _check_size(value: bytes | str) -> None:
    size = len(value if isinstance(value, bytes) else value.encode("utf-8"))
    if size > MAX_IMPORT_BYTES:
        raise CardImportError(f"Imported file exceeds the {MAX_IMPORT_BYTES} byte limit")


def _text(value: Any, field_name: str) -> str:
    if isinstance(value, (list, tuple)):
        result = "\n".join(str(item) for item in value)
    else:
        result = str(value or "")
    if len(result) > MAX_TEXT_LENGTH:
        raise CardImportError(f"Imported field is too long: {field_name}")
    return result


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _decode_itxt(chunk_data: bytes) -> tuple[str | None, str | None]:
    keyword_bytes, separator, remainder = chunk_data.partition(b"\x00")
    if not separator or len(remainder) < 2:
        return None, None
    compression_flag = remainder[0]
    remainder = remainder[2:]
    _language, separator, remainder = remainder.partition(b"\x00")
    if not separator:
        return None, None
    _translated, separator, text = remainder.partition(b"\x00")
    if not separator:
        return None, None
    try:
        if compression_flag == 1:
            text = zlib.decompress(text)
        return keyword_bytes.decode("latin-1", errors="ignore"), text.decode("utf-8", errors="strict")
    except (UnicodeDecodeError, zlib.error):
        return None, None


def _decode_png_card(data: bytes) -> tuple[dict[str, Any], str]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CardImportError("File is not a supported character card")
    offset = 8
    ranked_payloads: dict[int, str] = {}
    keyword_rank: dict[str, int] = {"chara": 1, "character": 2, "ccv2": 3}
    is_apng = False
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        start = offset + 8
        end = start + length
        if end + 4 > len(data):
            raise CardImportError("PNG card contains a truncated chunk")
        chunk_data = data[start:end]
        expected_crc = int.from_bytes(data[end : end + 4], "big")
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            raise CardImportError("PNG card contains an invalid chunk checksum")
        offset = end + 4
        if chunk_type == b"acTL":
            is_apng = True
        keyword: str | None = None
        text: str | None = None
        if chunk_type == b"tEXt" and b"\x00" in chunk_data:
            keyword_bytes, text_bytes = chunk_data.split(b"\x00", 1)
            keyword = keyword_bytes.decode("latin-1", errors="ignore")
            text = text_bytes.decode("latin-1", errors="ignore")
        elif chunk_type == b"iTXt":
            keyword, text = _decode_itxt(chunk_data)
        elif chunk_type == b"zTXt" and b"\x00" in chunk_data:
            keyword_bytes, remainder = chunk_data.split(b"\x00", 1)
            keyword = keyword_bytes.decode("latin-1", errors="ignore")
            if len(remainder) >= 2 and remainder[0] == 0:
                try:
                    text = zlib.decompress(remainder[1:]).decode("latin-1", errors="ignore")
                except zlib.error:
                    text = None
        if keyword in keyword_rank and text:
            rank = int(keyword_rank[keyword])
            ranked_payloads[rank] = text
    if not ranked_payloads:
        raise CardImportError("PNG does not contain a character card payload")
    card_payload = ranked_payloads[max(ranked_payloads)]
    try:
        stripped = "".join(card_payload.split())
        decoded = base64.b64decode(stripped + "=" * (-len(stripped) % 4), validate=False)
        value = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error):
        try:
            value = json.loads(card_payload)
        except (ValueError, UnicodeDecodeError) as fallback_exc:
            raise CardImportError("Embedded character card metadata is malformed") from fallback_exc
    if not isinstance(value, dict):
        raise CardImportError("Embedded character card metadata must be an object")
    _validate_json_limits(value)
    return value, "apng" if is_apng else "png"


def extract_embedded_card(data: bytes) -> tuple[dict[str, Any], str]:
    return _decode_png_card(data)


def extract_png_card(data: bytes) -> tuple[dict[str, Any], str]:
    return _decode_png_card(data)


def _normalize_book(raw_book: Any, warnings: list[str]) -> dict[str, Any] | None:
    if raw_book is None:
        return None
    if not isinstance(raw_book, dict):
        warnings.append("character_book was not an object and was ignored")
        return None
    raw_entries = raw_book.get("entries", [])
    if isinstance(raw_entries, dict):
        if "entries" in raw_entries:
            raw_entries = raw_entries.get("entries", [])
        else:
            raw_entries = list(raw_entries.values())
            warnings.append("character_book.entries was UID keyed and was converted to an array")
    if not isinstance(raw_entries, list):
        warnings.append("character_book.entries was not an array and was ignored")
        raw_entries = []
    if len(raw_entries) > MAX_BOOK_ENTRIES:
        warnings.append(f"character_book was truncated to {MAX_BOOK_ENTRIES} entries")
        raw_entries = raw_entries[:MAX_BOOK_ENTRIES]
    book_scan_depth = _as_int(raw_book.get("scan_depth", raw_book.get("scanDepth", 4)), 4)
    entries: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            warnings.append(f"character_book.entries[{index}] was ignored because it was not an object")
            continue
        explicit_extensions = raw_entry.get("extensions")
        entry_extensions = dict(explicit_extensions) if isinstance(explicit_extensions, dict) else {}
        metadata_fields = [key for key in raw_entry if key not in ENTRY_FIELDS]
        if metadata_fields:
            warnings.append(
                f"character_book.entries[{index}] preserved unsupported metadata: {', '.join(sorted(metadata_fields))}"
            )
        for key, value in raw_entry.items():
            if key not in ENTRY_FIELDS:
                entry_extensions.setdefault(key, value)
        order = _as_int(raw_entry.get("insertion_order", raw_entry.get("order", raw_entry.get("priority", index))), index)
        entries.append(
            {
                "id": raw_entry.get("id", index),
                "source_id": raw_entry.get("id", index),
                "keys": [
                    str(value)
                    for value in _as_list(
                        raw_entry.get("keys", raw_entry.get("primary_keys", raw_entry.get("key")))
                    )
                ],
                "secondary_keys": [
                    str(value)
                    for value in _as_list(raw_entry.get("secondary_keys", raw_entry.get("keysecondary")))
                ],
                "comment": _text(raw_entry.get("comment", raw_entry.get("name", "")), "entry name")[:MAX_ENTRY_NAME_LENGTH],
                "content": _text(raw_entry.get("content", ""), "entry content"),
                "constant": bool(raw_entry.get("constant", False)),
                "selective": bool(raw_entry.get("selective", True)),
                "enabled": bool(raw_entry.get("enabled", True)),
                "recursive": bool(raw_entry.get("recursive", raw_entry.get("recursive_scanning", True))),
                "order": order,
                "priority": _as_int(raw_entry.get("priority"), order),
                "position": raw_entry.get("position", 0),
                "probability": _as_float(raw_entry.get("probability"), 1.0),
                "scan_depth": _as_int(raw_entry.get("scan_depth"), book_scan_depth),
                "enabled_for_model": [str(value) for value in _as_list(raw_entry.get("enabled_for_model"))],
                "extensions": entry_extensions,
            }
        )
    return {
        "name": _text(raw_book.get("name", "Imported character book"), "book name")[:MAX_NAME_LENGTH],
        "description": _text(raw_book.get("description", ""), "book description"),
        "scan_depth": max(0, book_scan_depth),
        "token_budget": max(1, _as_int(raw_book.get("token_budget"), 2048)),
        "recursive_scanning": bool(raw_book.get("recursive_scanning", True)),
        "entries": entries,
    }


def import_character_card(value: bytes | str | dict[str, Any], filename: str = "") -> ImportedCharacterCard:
    _check_size(value) if isinstance(value, (bytes, str)) else None
    warnings: list[str] = []
    if isinstance(value, bytes):
        if value.startswith(b"\x89PNG"):
            raw, source_format = _decode_png_card(value)
        else:
            try:
                raw = json.loads(value.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CardImportError("Character card JSON is malformed") from exc
            source_format = "apng" if filename.lower().endswith(".apng") else "json"
    elif isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CardImportError("Character card JSON is malformed") from exc
        source_format = "apng" if filename.lower().endswith(".apng") else "json"
    else:
        raw = value
        source_format = "json"
    if not isinstance(raw, dict):
        raise CardImportError("Character card must be a JSON object")
    _validate_json_limits(raw)
    has_wrapper = isinstance(raw.get("data"), dict)
    data = raw.get("data") if has_wrapper else raw
    if not isinstance(data, dict):
        raise CardImportError("Character card data must be an object")
    name = str(data.get("name", raw.get("name", ""))).strip()
    if not name:
        raise CardImportError("Character card is missing a name")
    if len(name) > MAX_NAME_LENGTH:
        raise CardImportError(f"Character name exceeds the {MAX_NAME_LENGTH} character limit")
    definition = {
        "description": _text(data.get("description", ""), "description"),
        "personality": _text(data.get("personality", ""), "personality"),
        "scenario": _text(data.get("scenario", ""), "scenario"),
        "first_mes": _text(data.get("first_mes", data.get("first_message", "")), "first_mes"),
        "mes_example": _text(data.get("mes_example", data.get("example_messages", "")), "mes_example"),
        "alternate_greetings": [_text(value, "alternate greeting") for value in _as_list(data.get("alternate_greetings", data.get("alternate_mes")))],
        "system_prompt": _text(data.get("system_prompt", ""), "system_prompt"),
        "post_history_instructions": _text(data.get("post_history_instructions", ""), "post_history_instructions"),
        "creator_notes": _text(data.get("creator_notes", ""), "creator_notes"),
        "tags": [str(value) for value in _as_list(data.get("tags"))],
        "creator": _text(data.get("creator", ""), "creator"),
        "character_version": _text(data.get("character_version", ""), "character_version"),
    }
    nested_raw = raw.get("extensions")
    data_raw = data.get("extensions")
    nested_extensions: dict[str, Any] = nested_raw if isinstance(nested_raw, dict) else {}
    data_extensions: dict[str, Any] = data_raw if isinstance(data_raw, dict) else {}
    extensions = dict(nested_extensions)
    extensions.update(data_extensions)
    for key, extension_value in raw.items():
        if key not in KNOWN_FIELDS:
            extensions.setdefault(key, extension_value)
    for key, extension_value in data.items():
        if key not in KNOWN_FIELDS:
            extensions.setdefault(key, extension_value)
    book = _normalize_book(data.get("character_book", extensions.get("character_book")), warnings)
    spec = str(raw.get("spec", "")).casefold()
    spec_version = str(raw.get("spec_version", "")).casefold()
    if spec.startswith("chara_card_v2") or spec_version.startswith("2") or (has_wrapper and not spec):
        version = "2"
    else:
        version = "1"
    return ImportedCharacterCard(
        name=name,
        definition=definition,
        extensions=extensions,
        character_book=book,
        card_version=version,
        source_format=source_format,
        source_filename=filename,
        warnings=warnings,
        raw=raw,
    )


def import_lorebook(value: bytes | str | dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CardImportError("Lorebook must be UTF-8 JSON") from exc
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CardImportError("Lorebook JSON is malformed") from exc
    if isinstance(value, list):
        value = {"entries": value, "name": "Imported lorebook"}
    if not isinstance(value, dict):
        raise CardImportError("Lorebook must be a JSON object or array")
    _validate_json_limits(value)
    warnings: list[str] = []
    entries = value.get("entries", value.get("lorebook", []))
    if isinstance(entries, dict):
        if "entries" in entries:
            entries = entries.get("entries", [])
        else:
            entries = list(entries.values())
            warnings.append("lorebook entries were UID keyed and were converted to an array")
    if not isinstance(entries, list):
        raise CardImportError("Lorebook entries must be an array")
    normalized = _normalize_book({"entries": entries, **{key: item for key, item in value.items() if key != "entries"}}, warnings)
    if normalized is None:
        raise CardImportError("Lorebook could not be normalized")
    normalized["warnings"] = warnings
    return normalized


normalize_character_card = import_character_card
normalize_lorebook = import_lorebook
