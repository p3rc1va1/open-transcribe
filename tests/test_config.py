import json
import os

from src.config import (
    APP_DIR,
    CONFIG_PATH,
    RECORDINGS_DIR,
    REQUIRED_KEYS,
    SUMMARY_PROMPT,
    TITLE_PROMPT,
    TRANSCRIPTION_PROMPT,
    Config,
    load_config,
    save_config,
)

# ── Config dataclass ──────────────────────────────────────────────────


class TestConfig:
    def test_defaults(self):
        c = Config()
        assert c.gemini_api_key == ""
        assert c.gemini_model == ""
        assert c.notion_token == ""
        assert c.notion_database_id == ""

    def test_construction_with_values(self):
        c = Config(
            gemini_api_key="k",
            gemini_model="m",
            notion_token="t",
            notion_database_id="d",
        )
        assert c.gemini_api_key == "k"
        assert c.gemini_model == "m"
        assert c.notion_token == "t"
        assert c.notion_database_id == "d"


# ── REQUIRED_KEYS ─────────────────────────────────────────────────────


class TestRequiredKeys:
    def test_required_keys_explicit(self):
        assert REQUIRED_KEYS == ["gemini_api_key", "notion_token", "notion_database_id"]
        assert "gemini_model" not in REQUIRED_KEYS


# ── Module constants ──────────────────────────────────────────────────


class TestModuleConstants:
    def test_paths_are_paths(self):
        assert hasattr(APP_DIR, "exists")
        assert hasattr(CONFIG_PATH, "exists")
        assert hasattr(RECORDINGS_DIR, "exists")

    def test_prompts_nonempty(self):
        assert isinstance(TRANSCRIPTION_PROMPT, str) and len(TRANSCRIPTION_PROMPT) > 0
        assert isinstance(SUMMARY_PROMPT, str) and len(SUMMARY_PROMPT) > 0
        assert isinstance(TITLE_PROMPT, str) and len(TITLE_PROMPT) > 0


# ── load_config ───────────────────────────────────────────────────────


class TestLoadConfig:
    def test_missing_file(self, tmp_config_dir):
        config, missing = load_config()
        assert config is None
        assert missing == REQUIRED_KEYS[:]

    def test_malformed_json(self, tmp_config_dir):
        d = tmp_config_dir
        d["app_dir"].mkdir(parents=True)
        d["config_path"].write_text("not json")
        config, missing = load_config()
        assert config is None
        assert missing == REQUIRED_KEYS[:]

    def test_complete_config(self, tmp_config_dir, sample_config_dict):
        d = tmp_config_dir
        d["app_dir"].mkdir(parents=True)
        d["config_path"].write_text(json.dumps(sample_config_dict))
        config, missing = load_config()
        assert config is not None
        assert missing == []
        assert config.gemini_api_key == "test-gemini-key"

    def test_partial_config(self, tmp_config_dir):
        d = tmp_config_dir
        d["app_dir"].mkdir(parents=True)
        d["config_path"].write_text(json.dumps({"gemini_api_key": "key"}))
        config, missing = load_config()
        assert config is not None
        assert "notion_token" in missing
        assert "notion_database_id" in missing

    def test_empty_strings_count_as_missing(self, tmp_config_dir):
        d = tmp_config_dir
        d["app_dir"].mkdir(parents=True)
        d["config_path"].write_text(json.dumps({"gemini_api_key": "", "notion_token": "", "notion_database_id": ""}))
        config, missing = load_config()
        assert config is not None
        assert set(missing) == set(REQUIRED_KEYS)

    def test_extra_keys_ignored(self, tmp_config_dir, sample_config_dict):
        d = tmp_config_dir
        d["app_dir"].mkdir(parents=True)
        sample_config_dict["extra_key"] = "whatever"
        d["config_path"].write_text(json.dumps(sample_config_dict))
        config, missing = load_config()
        assert config is not None
        assert missing == []

    def test_os_error(self, tmp_config_dir):
        d = tmp_config_dir
        d["app_dir"].mkdir(parents=True)
        d["config_path"].write_text("{}")
        # Make the file unreadable to trigger OSError
        os.chmod(d["config_path"], 0o000)
        try:
            config, missing = load_config()
            assert config is None
            assert missing == REQUIRED_KEYS[:]
        finally:
            os.chmod(d["config_path"], 0o644)


# ── save_config ───────────────────────────────────────────────────────


class TestSaveConfig:
    def test_creates_dir_and_file(self, tmp_config_dir, sample_config):
        save_config(sample_config)
        assert tmp_config_dir["config_path"].exists()

    def test_correct_json_content(self, tmp_config_dir, sample_config, sample_config_dict):
        save_config(sample_config)
        data = json.loads(tmp_config_dir["config_path"].read_text())
        assert data == sample_config_dict

    def test_permissions(self, tmp_config_dir, sample_config):
        save_config(sample_config)
        assert oct(tmp_config_dir["app_dir"].stat().st_mode & 0o777) == oct(0o700)
        assert oct(tmp_config_dir["config_path"].stat().st_mode & 0o777) == oct(0o600)

    def test_overwrites_existing(self, tmp_config_dir, sample_config):
        tmp_config_dir["app_dir"].mkdir(parents=True)
        tmp_config_dir["config_path"].write_text("{}")
        save_config(sample_config)
        data = json.loads(tmp_config_dir["config_path"].read_text())
        assert data["gemini_api_key"] == "test-gemini-key"
