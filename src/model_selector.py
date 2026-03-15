import logging

from google import genai
from google.genai import errors as genai_errors

log = logging.getLogger("open-transcribe")

# Ordered best → most available. Used for sorting discovered models
# and as a fallback list when the API is unreachable.
MODEL_TIER_ORDER = [
    "gemini-3-pro",
    "gemini-3-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]


def _sort_by_tier(model_names: list[str]) -> list[str]:
    """Sort model names according to MODEL_TIER_ORDER.

    Models matching an earlier prefix rank higher.
    Models not matching any prefix are excluded.
    """
    def tier_key(name: str) -> tuple[int, str]:
        for i, prefix in enumerate(MODEL_TIER_ORDER):
            if name.startswith(prefix):
                return (i, name)
        return (len(MODEL_TIER_ORDER), name)

    ranked = sorted(model_names, key=tier_key)
    # Exclude models that don't match any tier prefix
    return [n for n in ranked if any(n.startswith(p) for p in MODEL_TIER_ORDER)]


def _discover_models(client: genai.Client) -> list[str]:
    """Fetch available models from the Gemini API, filtered to generateContent-capable ones."""
    models: list[str] = []
    for model in client.models.list():
        name = model.name or ""
        # API returns names like "models/gemini-2.5-flash"; strip the prefix
        if name.startswith("models/"):
            name = name[len("models/"):]
        actions = model.supported_actions or []
        if "generateContent" in actions:
            models.append(name)
    return models


def is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception is a 429 / RESOURCE_EXHAUSTED error."""
    return isinstance(exc, genai_errors.ClientError) and getattr(exc, "code", None) == 429


class ModelSelector:
    """Manages a tiered list of Gemini models with automatic fallback on rate limits."""

    def __init__(self, client: genai.Client, preferred_model: str = ""):
        try:
            discovered = _discover_models(client)
            self._models = _sort_by_tier(discovered)
        except Exception as e:
            log.warning(f"Failed to fetch model list from API: {e}. Using fallback tier list.")
            self._models = []

        if not self._models:
            # Use tier order prefixes directly as fallback model names
            self._models = list(MODEL_TIER_ORDER)
            log.info(f"Using fallback model list: {self._models}")

        # If user set a preferred model, move it to the front
        if preferred_model:
            if preferred_model in self._models:
                self._models.remove(preferred_model)
                self._models.insert(0, preferred_model)
            else:
                # Respect user intent: insert at front even if not in tier list
                log.warning(
                    f"Preferred model '{preferred_model}' not found in available models. "
                    f"Adding it as primary anyway."
                )
                self._models.insert(0, preferred_model)

        self._current_index = 0
        log.info(f"Model tier list: {self._models}")

    @property
    def current_model(self) -> str:
        """Return the model name currently in use."""
        return self._models[self._current_index]

    @property
    def models(self) -> list[str]:
        """Return the full ordered model list."""
        return list(self._models)

    def advance_on_rate_limit(self) -> bool:
        """Advance to the next model after a 429 error.

        Returns True if there is a next model to try, False if all exhausted.
        """
        if self._current_index + 1 < len(self._models):
            self._current_index += 1
            log.warning(f"Rate limited. Falling back to: {self.current_model}")
            return True
        log.error("All models in the tier list are rate-limited.")
        return False

    def reset(self) -> None:
        """Reset to the top of the tier list (called at the start of each new job)."""
        self._current_index = 0
