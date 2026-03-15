from unittest.mock import MagicMock, patch

import pytest

from src.model_selector import (
    MODEL_TIER_ORDER,
    ModelSelector,
    _sort_by_tier,
    is_rate_limit_error,
)


# ── MODEL_TIER_ORDER ─────────────────────────────────────────────────

class TestModelTierOrder:
    def test_has_entries(self):
        assert len(MODEL_TIER_ORDER) > 0

    def test_all_strings(self):
        for entry in MODEL_TIER_ORDER:
            assert isinstance(entry, str) and len(entry) > 0

    def test_ordered_best_to_available(self):
        assert MODEL_TIER_ORDER[0].startswith("gemini-3")
        assert MODEL_TIER_ORDER[-1].startswith("gemini-2.0")


# ── _sort_by_tier ────────────────────────────────────────────────────

class TestSortByTier:
    def test_sorts_by_tier_order(self):
        models = ["gemini-2.0-flash", "gemini-3-pro-preview", "gemini-2.5-flash"]
        result = _sort_by_tier(models)
        assert result[0] == "gemini-3-pro-preview"
        assert result[1] == "gemini-2.5-flash"
        assert result[2] == "gemini-2.0-flash"

    def test_excludes_unrecognized(self):
        models = ["gemini-2.5-flash", "imagen-3.0", "gemini-embedding-001"]
        result = _sort_by_tier(models)
        assert result == ["gemini-2.5-flash"]

    def test_empty_input(self):
        assert _sort_by_tier([]) == []

    def test_multiple_same_tier(self):
        models = ["gemini-3-flash-preview", "gemini-3-flash-lite"]
        result = _sort_by_tier(models)
        assert len(result) == 2
        # Both should be present, sorted alphabetically within tier
        assert all(m.startswith("gemini-3-flash") for m in result)


# ── is_rate_limit_error ──────────────────────────────────────────────

def _make_client_error(code: int):
    from google.genai import errors as genai_errors
    return genai_errors.ClientError(code, {"error": {"message": f"test {code}"}})


def _make_server_error(code: int):
    from google.genai import errors as genai_errors
    return genai_errors.ServerError(code, {"error": {"message": f"test {code}"}})


class TestIsRateLimitError:
    def test_client_error_429(self):
        assert is_rate_limit_error(_make_client_error(429)) is True

    def test_client_error_400(self):
        assert is_rate_limit_error(_make_client_error(400)) is False

    def test_runtime_error(self):
        assert is_rate_limit_error(RuntimeError("fail")) is False

    def test_server_error(self):
        assert is_rate_limit_error(_make_server_error(500)) is False


# ── ModelSelector ────────────────────────────────────────────────────

def _make_mock_model(name: str, actions: list[str] | None = None):
    """Create a mock Model object as returned by client.models.list()."""
    m = MagicMock()
    m.name = f"models/{name}"
    m.supported_actions = actions or ["generateContent"]
    return m


class TestModelSelectorInit:
    def test_discovers_and_sorts_models(self):
        client = MagicMock()
        client.models.list.return_value = [
            _make_mock_model("gemini-2.0-flash"),
            _make_mock_model("gemini-3-flash-preview"),
            _make_mock_model("gemini-2.5-pro"),
        ]
        selector = ModelSelector(client)
        assert selector.models[0] == "gemini-3-flash-preview"
        assert selector.models[1] == "gemini-2.5-pro"
        assert selector.models[2] == "gemini-2.0-flash"

    def test_filters_non_generate_content(self):
        client = MagicMock()
        client.models.list.return_value = [
            _make_mock_model("gemini-2.5-flash"),
            _make_mock_model("gemini-embedding-001", actions=["embedContent"]),
        ]
        selector = ModelSelector(client)
        assert "gemini-embedding-001" not in selector.models
        assert "gemini-2.5-flash" in selector.models

    def test_filters_non_tier_models(self):
        client = MagicMock()
        client.models.list.return_value = [
            _make_mock_model("gemini-2.5-flash"),
            _make_mock_model("imagen-3.0-generate"),
            _make_mock_model("veo-3.1-generate-preview"),
        ]
        selector = ModelSelector(client)
        assert selector.models == ["gemini-2.5-flash"]

    def test_current_model_starts_at_first(self):
        client = MagicMock()
        client.models.list.return_value = [
            _make_mock_model("gemini-2.5-flash"),
            _make_mock_model("gemini-2.0-flash"),
        ]
        selector = ModelSelector(client)
        assert selector.current_model == "gemini-2.5-flash"


class TestModelSelectorPreferredModel:
    def test_preferred_model_moves_to_front(self):
        client = MagicMock()
        client.models.list.return_value = [
            _make_mock_model("gemini-3-flash-preview"),
            _make_mock_model("gemini-2.5-flash"),
            _make_mock_model("gemini-2.0-flash"),
        ]
        selector = ModelSelector(client, preferred_model="gemini-2.0-flash")
        assert selector.current_model == "gemini-2.0-flash"
        assert "gemini-3-flash-preview" in selector.models

    def test_preferred_model_not_in_list_added_anyway(self):
        client = MagicMock()
        client.models.list.return_value = [
            _make_mock_model("gemini-2.5-flash"),
        ]
        selector = ModelSelector(client, preferred_model="gemini-custom-model")
        assert selector.current_model == "gemini-custom-model"
        assert "gemini-2.5-flash" in selector.models

    def test_empty_preferred_uses_default_order(self):
        client = MagicMock()
        client.models.list.return_value = [
            _make_mock_model("gemini-2.0-flash"),
            _make_mock_model("gemini-3-flash-preview"),
        ]
        selector = ModelSelector(client, preferred_model="")
        assert selector.current_model == "gemini-3-flash-preview"


class TestModelSelectorFallback:
    def test_api_failure_uses_tier_order(self):
        client = MagicMock()
        client.models.list.side_effect = RuntimeError("network error")
        selector = ModelSelector(client)
        assert selector.models == list(MODEL_TIER_ORDER)

    def test_empty_discovery_uses_tier_order(self):
        client = MagicMock()
        # Return only non-tier models so filtered list is empty
        client.models.list.return_value = [
            _make_mock_model("imagen-3.0"),
        ]
        selector = ModelSelector(client)
        assert selector.models == list(MODEL_TIER_ORDER)


class TestAdvanceOnRateLimit:
    def test_advances_to_next(self):
        client = MagicMock()
        client.models.list.return_value = [
            _make_mock_model("gemini-3-flash-preview"),
            _make_mock_model("gemini-2.5-flash"),
        ]
        selector = ModelSelector(client)
        assert selector.current_model == "gemini-3-flash-preview"
        assert selector.advance_on_rate_limit() is True
        assert selector.current_model == "gemini-2.5-flash"

    def test_returns_false_when_exhausted(self):
        client = MagicMock()
        client.models.list.return_value = [
            _make_mock_model("gemini-2.5-flash"),
        ]
        selector = ModelSelector(client)
        assert selector.advance_on_rate_limit() is False

    def test_multiple_advances(self):
        client = MagicMock()
        client.models.list.return_value = [
            _make_mock_model("gemini-3-flash-preview"),
            _make_mock_model("gemini-2.5-flash"),
            _make_mock_model("gemini-2.0-flash"),
        ]
        selector = ModelSelector(client)
        assert selector.advance_on_rate_limit() is True
        assert selector.advance_on_rate_limit() is True
        assert selector.advance_on_rate_limit() is False


class TestReset:
    def test_resets_to_first(self):
        client = MagicMock()
        client.models.list.return_value = [
            _make_mock_model("gemini-3-flash-preview"),
            _make_mock_model("gemini-2.5-flash"),
        ]
        selector = ModelSelector(client)
        selector.advance_on_rate_limit()
        assert selector.current_model == "gemini-2.5-flash"
        selector.reset()
        assert selector.current_model == "gemini-3-flash-preview"
