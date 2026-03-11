import atexit
import logging
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import rumps

from config import Config, TRANSCRIPTION_PROMPT, APP_DIR, CONFIG_PATH, load_config, save_config
from recorder import AudioRecorder
from transcriber import TranscriptionService, TranscriptionError
from notion_service import NotionService, save_transcription_locally

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("open-transcribe")

PID_FILE = APP_DIR / "app.pid"


def _ensure_info_plist():
    """Create Info.plist next to the Python binary so rumps notifications work."""
    plist_path = Path(sys.executable).parent / "Info.plist"
    subprocess.run(
        [
            "/usr/libexec/PlistBuddy",
            "-c",
            'Add :CFBundleIdentifier string "com.open-transcribe"',
            str(plist_path),
        ],
        check=False,
        capture_output=True,
    )


def _kill_previous_instance():
    """Kill any previous instance using the PID file."""
    try:
        old_pid = int(PID_FILE.read_text().strip())
        if old_pid == os.getpid():
            return
        log.info(f"Killing previous instance (PID {old_pid})")
        os.kill(old_pid, signal.SIGTERM)
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        pass  # stale/missing PID file or already dead
    finally:
        PID_FILE.unlink(missing_ok=True)


def _write_pid():
    """Write current PID to file."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def _cleanup_pid():
    """Remove PID file on exit."""
    PID_FILE.unlink(missing_ok=True)


ICON_PATH = str(Path(__file__).parent / "media" / "menu_icon.png")


class OpenTranscribeApp(rumps.App):
    def __init__(self):
        super().__init__("Open Transcribe", icon=ICON_PATH, quit_button=None)
        self.menu = [
            rumps.MenuItem("Start Recording", callback=self.toggle_recording),
            None,  # separator
            rumps.MenuItem("Settings", callback=self.open_settings),
            rumps.MenuItem("Reload Config", callback=self.reload_config),
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

        self._recorder = AudioRecorder()
        self._transcriber: TranscriptionService | None = None
        self._notion: NotionService | None = None
        self._config: Config | None = None
        self._recording_start: datetime | None = None

        self._load_and_init()

    def _load_and_init(self):
        config, missing = load_config()
        log.info(f"Config loaded. Missing keys: {missing or 'none'}")
        if config and not missing:
            self._config = config
            self._init_services(config)
        else:
            self._config = config or Config()

    def _init_services(self, config: Config):
        log.info("Initializing services...")
        self._transcriber = TranscriptionService(config.gemini_api_key)
        self._notion = NotionService(config.notion_token, config.notion_database_id)
        log.info("Services ready.")

    # ── Recording toggle ──────────────────────────────────────────────

    def toggle_recording(self, sender):
        if not self._recorder.is_recording:
            self._start_recording(sender)
        else:
            self._stop_recording(sender)

    def _start_recording(self, sender):
        if not self._transcriber:
            log.warning("Tried to record without config")
            rumps.notification("Open Transcribe", "Not configured", "Click Settings, add keys, then Reload Config.")
            return

        self._recording_start = datetime.now()
        timestamp = self._recording_start.strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"meeting_{timestamp}.wav"

        try:
            filepath = self._recorder.start(filename)
            log.info(f"Recording started: {filepath} (device: {self._recorder.device_name})")
        except Exception as e:
            log.error(f"Recording failed to start: {e}")
            rumps.notification("Open Transcribe", "Recording failed", str(e))
            return

        self.title = "REC"
        sender.title = "Stop Recording"
        rumps.notification(
            "Open Transcribe",
            "Recording started",
            f"Device: {self._recorder.device_name}",
        )

    def _stop_recording(self, sender):
        audio_path = self._recorder.stop()
        recording_end = datetime.now()
        self.title = "..."
        sender.title = "Start Recording"
        log.info(f"Recording stopped: {audio_path}")

        if not audio_path:
            log.warning("No audio path returned")
            self.title = ""
            return

        duration = (recording_end - self._recording_start).total_seconds()
        log.info(f"Duration: {duration:.1f}s")
        date = self._recording_start
        title = f"Meeting - {date.strftime('%Y-%m-%d %H:%M')}"

        # Process in background thread so UI stays responsive
        thread = threading.Thread(
            target=self._process_recording,
            args=(audio_path, title, date, duration),
            daemon=True,
        )
        thread.start()

    # ── Background processing ─────────────────────────────────────────

    def _process_recording(self, audio_path: str, title: str, date: datetime, duration: float):
        log.info(f"Transcribing {audio_path}...")
        try:
            text = self._transcriber.transcribe(audio_path, TRANSCRIPTION_PROMPT)
            log.info(f"Transcription complete ({len(text)} chars)")
        except TranscriptionError as e:
            log.error(f"Transcription failed: {e}")
            rumps.notification("Open Transcribe", "Transcription failed", str(e))
            self.title = ""
            return

        # Save to Notion
        log.info("Saving to Notion...")
        try:
            url = self._notion.save_transcription(title, date, duration, text)
            log.info(f"Saved to Notion: {url}")
            rumps.notification("Open Transcribe", "Saved to Notion", title)
        except Exception as e:
            log.error(f"Notion save failed: {e}")
            # Fallback: save locally
            local_path = save_transcription_locally(title, date, duration, text)
            log.info(f"Saved locally: {local_path}")
            rumps.notification(
                "Open Transcribe",
                "Notion failed — saved locally",
                f"{local_path}\n{e}",
            )

        self.title = ""

    # ── Settings ──────────────────────────────────────────────────────

    def open_settings(self, sender):
        """Open config.json in the default editor. Creates a template if it doesn't exist."""
        save_config(self._config or Config())
        log.info(f"Opening config: {CONFIG_PATH}")
        subprocess.Popen(["open", str(CONFIG_PATH)])

    def reload_config(self, sender):
        """Reload config from disk after the user edits it."""
        self._load_and_init()
        if self._transcriber:
            self.title = ""
            log.info("Config reloaded successfully")
        else:
            log.warning("Config reload: missing keys")


def main():
    _kill_previous_instance()
    _write_pid()
    if ".app/Contents" not in sys.executable:
        _ensure_info_plist()
    atexit.register(_cleanup_pid)

    log.info("Starting Open Transcribe...")
    app = OpenTranscribeApp()
    app.run()


if __name__ == "__main__":
    main()
