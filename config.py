import json
import os
from dataclasses import dataclass, fields, asdict
from pathlib import Path

APP_DIR = Path.home() / ".open-transcribe"
CONFIG_PATH = APP_DIR / "config.json"
RECORDINGS_DIR = Path.home() / "Documents" / "Open Transcribe"

TRANSCRIPTION_PROMPT = (
    "Process this audio recording of a meeting in two parts:\n\n"
    "## Transcription\n"
    "Transcribe the full audio verbatim. "
    "Include speaker labels where possible (Speaker 1, Speaker 2, etc.). "
    "Use proper punctuation and paragraphs.\n\n"
    "## AI Summary\n"
    "After the transcription, provide a comprehensive summary including:\n"
    "- **Overview**: 2-3 sentence summary of what was discussed\n"
    "- **Key Topics**: Main subjects covered\n"
    "- **Decisions Made**: Any decisions that were agreed upon\n"
    "- **Action Items**: Tasks assigned, with owners if mentioned\n"
    "- **Open Questions**: Unresolved items or follow-ups needed\n"
)


@dataclass
class Config:
    gemini_api_key: str = ""
    notion_token: str = ""
    notion_database_id: str = ""


REQUIRED_KEYS = [f.name for f in fields(Config)]


def load_config() -> tuple[Config | None, list[str]]:
    """Load config from JSON. Returns (config, missing_keys)."""
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return None, REQUIRED_KEYS[:]

    config = Config(
        gemini_api_key=data.get("gemini_api_key", ""),
        notion_token=data.get("notion_token", ""),
        notion_database_id=data.get("notion_database_id", ""),
    )

    missing = [k for k in REQUIRED_KEYS if not getattr(config, k)]
    return config, missing


def save_config(config: Config) -> None:
    """Write config to JSON on disk with restricted permissions."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(APP_DIR, 0o700)
    CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2) + "\n")
    os.chmod(CONFIG_PATH, 0o600)
