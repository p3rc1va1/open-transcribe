from dataclasses import asdict
from unittest.mock import patch

import pytest

from src.config import Config


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Patch APP_DIR, CONFIG_PATH, and RECORDINGS_DIR to temp directories."""
    app_dir = tmp_path / ".open-transcribe"
    config_path = app_dir / "config.json"
    recordings_dir = tmp_path / "recordings"

    with (
        patch("src.config.APP_DIR", app_dir),
        patch("src.config.CONFIG_PATH", config_path),
        patch("src.config.RECORDINGS_DIR", recordings_dir),
    ):
        yield {
            "app_dir": app_dir,
            "config_path": config_path,
            "recordings_dir": recordings_dir,
        }


@pytest.fixture
def sample_config():
    """Return a fully populated Config instance."""
    return Config(
        gemini_api_key="test-gemini-key",
        gemini_model="",
        notion_token="test-notion-token",
        notion_database_id="test-db-id",
    )


@pytest.fixture
def sample_config_dict(sample_config):
    """Return the dict equivalent of sample_config for writing JSON."""
    return asdict(sample_config)
