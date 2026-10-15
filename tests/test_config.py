from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.api.app.config import Settings

BLANK_ENV = {
    "NARRATIVE_PROVIDER_CONTEXT_WINDOW": "",
    "NARRATIVE_PROVIDER_MAX_OUTPUT_TOKENS": "",
}


def test_env_example_defaults_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped .env.example must load as-is.

    Every variable in that file is empty or a literal, and a new setup copies it
    without editing. An empty numeric variable used to raise instead of meaning
    "not declared", so the first documented step failed before any other.
    """
    for key, value in BLANK_ENV.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)

    assert settings.provider_context_window == 0
    assert settings.provider_max_output_tokens == 0
    assert settings.provider_name == "heuristic"


def test_declared_token_counts_still_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARRATIVE_PROVIDER_CONTEXT_WINDOW", "128000")
    monkeypatch.setenv("NARRATIVE_PROVIDER_MAX_OUTPUT_TOKENS", "4096")

    settings = Settings(_env_file=None)

    assert settings.provider_context_window == 128000
    assert settings.provider_max_output_tokens == 4096


def test_whitespace_only_token_count_is_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NARRATIVE_PROVIDER_CONTEXT_WINDOW", "   ")

    assert Settings(_env_file=None).provider_context_window == 0


def test_non_numeric_token_count_is_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARRATIVE_PROVIDER_CONTEXT_WINDOW", "lots")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "raw,expected",
    [("", 120.0), ("   ", 120.0), ("0", 120.0), ("-5", 120.0), ("900", 900.0), ("12.5", 12.5)],
)
def test_provider_timeout_reads_a_blank_or_zero_value_as_the_default(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
) -> None:
    """A copied-from-template blank timeout must not become a request that never ends.

    Compose passes every unset provider variable through as an empty string, so
    an unset timeout arrives as ``""`` rather than being absent. Zero would be a
    request with no deadline at all, which is worse than the default rather than
    a stricter version of it.
    """
    monkeypatch.setenv("NARRATIVE_PROVIDER_TIMEOUT_SECONDS", raw)

    assert Settings(_env_file=None).provider_timeout_seconds == expected


def test_provider_timeout_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NARRATIVE_PROVIDER_TIMEOUT_SECONDS", raising=False)

    assert Settings(_env_file=None).provider_timeout_seconds == 120.0


def test_non_numeric_provider_timeout_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARRATIVE_PROVIDER_TIMEOUT_SECONDS", "soon")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_provider_name_reads_the_documented_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The setup docs name NARRATIVE_PROVIDER, so the API must honor it.

    The field is provider_name, which pydantic-settings otherwise reads from
    NARRATIVE_PROVIDER_NAME, a name nothing in the repo references. Every
    documented setup therefore silently fell back to heuristic: the API
    started, reported healthy, and never contacted a model. Both spellings
    are accepted now, and this test pins the documented one.
    """
    monkeypatch.setenv("NARRATIVE_PROVIDER", "ollama")
    monkeypatch.delenv("NARRATIVE_PROVIDER_NAME", raising=False)

    assert Settings(_env_file=None).provider_name == "ollama"


def test_provider_full_name_wins_over_the_short_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARRATIVE_PROVIDER_NAME", "ollama")
    monkeypatch.setenv("NARRATIVE_PROVIDER", "generic")

    assert Settings(_env_file=None).provider_name == "ollama"


def test_provider_name_still_accepts_field_name_init() -> None:
    assert Settings(provider_name="ollama").provider_name == "ollama"


def test_env_example_loads_with_the_provider_timeout_field_present() -> None:
    """The shipped template now names a timeout; a straight copy must still load."""
    template = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "NARRATIVE_PROVIDER_TIMEOUT_SECONDS=" in template

    for line in template.splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        assert Settings(**{key: value or None} if value else {key: None}) is not None
