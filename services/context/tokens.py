"""Token accounting for narrative context.

The engine cannot know exactly how a provider will tokenize a prompt, so it
estimates and says so. Three things matter for reliability:

- the estimate must be **deterministic**, so two runs of the same scenario
  assemble the same budget and the evaluation harness is reproducible;
- the estimate must be **attributable**, so a report can state which estimator
  produced a number rather than implying a precision it does not have;
- a **fallback** must always exist, so a missing or broken tokenizer library
  degrades to a documented heuristic instead of raising.

`estimator_for` prefers a provider-aware exact tokenizer when one is installed
and falls back to :class:`HeuristicTokenEstimator` otherwise. Nothing here
imports a tokenizer at module import time.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# Chat formats spend a small fixed number of tokens per message on role and
# delimiter markup, plus a priming cost for the reply. They are counted once per
# message rather than estimated from content so budget maths stays honest.
TOKENS_PER_MESSAGE = 4
TOKENS_PER_REPLY = 3

_WORD = re.compile(r"[\w'-]+", re.UNICODE)
_CJK = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]"
)

# OpenAI-compatible providers most commonly front BPE vocabularies in which an
# English word is roughly 1.3 tokens. The heuristic errs slightly high so that a
# prompt is pruned before a provider rejects it rather than after.
ENGLISH_TOKENS_PER_WORD = 1.3
# A character with no whitespace is close to one token per character.
DENSE_TOKENS_PER_CHARACTER = 1.0

# Fallback context window for a provider that declares nothing. Deliberately
# modest: assuming a large window is how an engine ends up silently exceeding it.
DEFAULT_CONTEXT_WINDOW = 8_192
DEFAULT_RESERVED_OUTPUT_TOKENS = 1_024
MIN_INPUT_BUDGET = 512


@dataclass(frozen=True)
class TokenEstimate:
    """A token count plus the identity of the estimator that produced it."""

    tokens: int
    estimator: str

    def to_dict(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "estimator": self.estimator}


@runtime_checkable
class TokenEstimator(Protocol):
    name: str

    def count(self, text: str) -> int: ...


class HeuristicTokenEstimator:
    """Tokenizer-free estimate: word count for spaced scripts, char count for dense scripts.

    This is the fallback that must always work. It is named rather than anonymous
    so a report never claims provider-exact numbers it does not have.
    """

    name = "heuristic-v1"

    def count(self, text: str) -> int:
        if not text:
            return 0
        words = _WORD.findall(text)
        dense = len(_CJK.findall(text))
        spaced = max(0, len(words) - dense)
        return max(1, round(spaced * ENGLISH_TOKENS_PER_WORD + dense * DENSE_TOKENS_PER_CHARACTER))

    def count_messages(self, messages: Sequence[Any]) -> int:
        total = TOKENS_PER_REPLY
        for message in messages:
            total += TOKENS_PER_MESSAGE + self.count(getattr(message, "content", ""))
        return total

    def count_json(self, value: Any) -> int:
        import json

        return self.count(json.dumps(value, sort_keys=True, default=str))


class TiktokenEstimator:
    """Exact counting through ``tiktoken`` when the library is installed.

    Constructed through :func:`estimator_for`; the caller never imports
    ``tiktoken`` directly, and a missing library is a normal condition rather
    than an error.
    """

    name = "tiktoken"

    def __init__(self, encoding: Any, label: str) -> None:
        self._encoding = encoding
        self.name = f"tiktoken:{label}"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._encoding.encode(text))

    def count_messages(self, messages: Sequence[Any]) -> int:
        total = TOKENS_PER_REPLY
        for message in messages:
            total += TOKENS_PER_MESSAGE + self.count(getattr(message, "content", ""))
        return total

    def count_json(self, value: Any) -> int:
        import json

        return self.count(json.dumps(value, sort_keys=True, default=str))


HEURISTIC = HeuristicTokenEstimator()

# Model-name fragments mapped to a tiktoken encoding. A provider that does not
# appear here still resolves, just through the fallback.
_ENCODING_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-4"), "o200k_base"),
    (("gpt-3.5", "text-embedding-ada", "davinci", "babbage"), "cl100k_base"),
)


def _tiktoken_encoding(model: str) -> tuple[Any, str] | None:
    try:
        import tiktoken
    except Exception:
        return None
    lowered = model.casefold()
    for fragments, label in _ENCODING_HINTS:
        if any(fragment in lowered for fragment in fragments):
            try:
                return tiktoken.get_encoding(label), label
            except Exception:
                return None
    return None


def estimator_for(model: str = "", *, allow_exact: bool = True) -> TokenEstimator:
    """Return the best available estimator for a model name.

    ``allow_exact=False`` forces the fallback, which the deterministic suite
    uses so that scenario reports do not change shape when a tokenizer library
    happens to be installed on the machine.
    """
    if allow_exact and model:
        resolved = _tiktoken_encoding(model)
        if resolved is not None:
            encoding, label = resolved
            return TiktokenEstimator(encoding, label)
    return HEURISTIC


def estimate_tokens(text: str, estimator: TokenEstimator | None = None) -> int:
    return (estimator or HEURISTIC).count(text)


def estimate_json(value: Any, estimator: TokenEstimator | None = None) -> int:
    import json

    return (estimator or HEURISTIC).count(json.dumps(value, sort_keys=True, default=str))


def estimate_messages(messages: Iterable[Any], estimator: TokenEstimator | None = None) -> int:
    active = estimator or HEURISTIC
    counter = getattr(active, "count_messages", None)
    if counter is not None:
        return int(counter(list(messages)))
    total = TOKENS_PER_REPLY
    for message in messages:
        total += TOKENS_PER_MESSAGE + active.count(getattr(message, "content", ""))
    return total


@dataclass(frozen=True)
class TokenBudget:
    """The provider-declared limits the assembler must respect."""

    context_window: int = DEFAULT_CONTEXT_WINDOW
    reserved_output_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS
    model: str = ""
    source: str = "default"

    @property
    def max_input_tokens(self) -> int:
        return max(MIN_INPUT_BUDGET, self.context_window - self.reserved_output_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_window": self.context_window,
            "reserved_output_tokens": self.reserved_output_tokens,
            "max_input_tokens": self.max_input_tokens,
            "model": self.model,
            "source": self.source,
        }

    @classmethod
    def from_capabilities(cls, capabilities: Any) -> TokenBudget:
        window = int(getattr(capabilities, "context_window", 0) or 0)
        output = int(getattr(capabilities, "max_output_tokens", 0) or 0)
        model = str(getattr(capabilities, "model", "") or "")
        if window <= 0:
            return cls(model=model, source="provider-default")
        return cls(
            context_window=window,
            reserved_output_tokens=output if output > 0 else DEFAULT_RESERVED_OUTPUT_TOKENS,
            model=model,
            source="provider",
        )
