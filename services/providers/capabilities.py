"""Provider capability metadata.

The context assembler must know the model's real context window rather than
assuming one universal size, and it must know what the provider can actually do
so the pipeline can degrade instead of failing.

Nothing here stores a credential. Capabilities are declared limits — window,
output ceiling, feature support — and are the only provider facts that are safe
to persist alongside a generation trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.context.tokens import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_RESERVED_OUTPUT_TOKENS,
    TokenBudget,
)

# Feature names are stable strings so a stored capability list stays comparable
# across releases even as the set of providers grows.
FEATURE_STRUCTURED_OUTPUT = "structured_output"
FEATURE_STREAMING = "streaming"
FEATURE_TOOLS = "tools"
FEATURE_JSON_MODE = "json_mode"
FEATURE_EMBEDDINGS = "embeddings"
KNOWN_FEATURES = frozenset(
    {
        FEATURE_STRUCTURED_OUTPUT,
        FEATURE_STREAMING,
        FEATURE_TOOLS,
        FEATURE_JSON_MODE,
        FEATURE_EMBEDDINGS,
    }
)

# A window below this is treated as a misconfiguration rather than a real limit.
MIN_WINDOW = 512

# Conservative defaults for an undeclared provider. The point of a conservative
# default is that an unconfigured studio still produces bounded prompts.
DEFAULT_CAPABILITIES: dict[str, Any] = {
    "context_window": DEFAULT_CONTEXT_WINDOW,
    "max_output_tokens": DEFAULT_RESERVED_OUTPUT_TOKENS,
    "features": [FEATURE_STRUCTURED_OUTPUT, FEATURE_JSON_MODE, FEATURE_STREAMING],
}


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can do, and how much room it has."""

    model: str = ""
    adapter: str = "heuristic"
    context_window: int = DEFAULT_CONTEXT_WINDOW
    max_output_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS
    features: frozenset[str] = field(default_factory=lambda: frozenset(DEFAULT_CAPABILITIES["features"]))
    declared: bool = False

    def supports(self, feature: str) -> bool:
        return feature in self.features

    def require(self, feature: str) -> bool:
        """Whether a feature is declared *and* recognised.

        An unrecognised feature name is treated as unsupported rather than
        optimistically enabled, so a typo in configuration cannot cause the
        pipeline to call something the provider will refuse.
        """
        return feature in KNOWN_FEATURES and feature in self.features

    def without(self, *features: str) -> ProviderCapabilities:
        return ProviderCapabilities(
            model=self.model,
            adapter=self.adapter,
            context_window=self.context_window,
            max_output_tokens=self.max_output_tokens,
            features=self.features - set(features),
            declared=self.declared,
        )

    def token_budget(self) -> TokenBudget:
        return TokenBudget.from_capabilities(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "adapter": self.adapter,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "features": sorted(self.features),
            "declared": self.declared,
            "supports_structured_output": self.require(FEATURE_STRUCTURED_OUTPUT),
            "supports_streaming": self.require(FEATURE_STREAMING),
            "supports_tools": self.require(FEATURE_TOOLS),
        }


def _coerce_int(value: Any, fallback: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > minimum else fallback


def capabilities_from_mapping(
    payload: dict[str, Any] | None,
    *,
    model: str = "",
    adapter: str = "",
) -> ProviderCapabilities:
    """Build capabilities from a stored ``ModelProvider`` row or settings block."""
    payload = dict(payload or {})
    features = payload.get("features") or payload.get("capabilities") or []
    normalised: set[str] = {str(feature).casefold() for feature in features if str(feature)}
    window = _coerce_int(
        payload.get("context_window"),
        DEFAULT_CONTEXT_WINDOW,
        minimum=MIN_WINDOW,
    )
    return ProviderCapabilities(
        model=str(payload.get("model") or model or ""),
        adapter=str(payload.get("adapter") or adapter or ""),
        context_window=window,
        max_output_tokens=_coerce_int(
            payload.get("max_output_tokens"),
            DEFAULT_RESERVED_OUTPUT_TOKENS,
            minimum=0,
        ),
        features=frozenset(normalised) if normalised else frozenset(DEFAULT_CAPABILITIES["features"]),
        declared=bool(payload) and bool(normalised or payload.get("context_window")),
    )


# The offline provider is the reference implementation: it produces a small
# structured object, so a studio running on it should budget tightly rather than
# assume a 100 000-token window it will never fill.
HEURISTIC_CAPABILITIES = ProviderCapabilities(
    model="heuristic",
    adapter="heuristic",
    context_window=8_192,
    max_output_tokens=1_024,
    features=frozenset({FEATURE_STRUCTURED_OUTPUT, FEATURE_JSON_MODE}),
    declared=True,
)


def capabilities_for(provider: Any, *, model: str = "", adapter: str = "") -> ProviderCapabilities:
    """Read capabilities from a live provider object, or fall back.

    A provider may expose ``capabilities`` as a mapping or as an object with
    attributes. Both are accepted; anything unrecognised yields the conservative
    default rather than an exception, because a missing capability declaration
    must never stop a generation.
    """
    declared = getattr(provider, "capabilities", None)
    if isinstance(declared, ProviderCapabilities):
        return declared
    if isinstance(declared, dict) and declared:
        return capabilities_from_mapping(
            declared,
            model=model or str(getattr(provider, "model", "") or ""),
            adapter=adapter or type(provider).__name__,
        )
    resolved_model = model or str(getattr(provider, "model", "") or "")
    if not resolved_model and type(provider).__name__ == "HeuristicProvider":
        return HEURISTIC_CAPABILITIES
    return ProviderCapabilities(
        model=resolved_model,
        adapter=adapter or type(provider).__name__,
        declared=False,
    )


def degradation_plan(capabilities: ProviderCapabilities) -> list[str]:
    """What the pipeline will do differently because of what is missing.

    Reported rather than silently applied, so a user can see why a studio run
    looks the way it does on a given provider.
    """
    notes: list[str] = []
    if not capabilities.require(FEATURE_STRUCTURED_OUTPUT):
        # This note previously claimed the pipeline "falls back to prose-only
        # beats if parsing fails". No such path exists: a JSON decode error
        # becomes a ProviderError and the generation is recorded as failed. The
        # trace is documentation a user reads when a studio run looks wrong, so a
        # note that describes behaviour the engine does not have is worse than no
        # note at all.
        notes.append(
            "Provider does not declare structured output; the pipeline will request JSON and "
            "validate the result. A response that cannot be parsed fails the generation rather "
            "than degrading to prose, so retry or regenerate from the reported recovery actions."
        )
    if not capabilities.require(FEATURE_STREAMING):
        notes.append("Provider does not declare streaming; generations are requested in one call.")
    if not capabilities.require(FEATURE_TOOLS):
        notes.append("Provider does not declare tool use; planning stays in the deterministic selector.")
    if not capabilities.declared:
        notes.append(
            f"No capability metadata declared; assuming a {capabilities.context_window}-token "
            "context window. Configure the provider to raise or lower this."
        )
    return notes
