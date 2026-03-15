import atexit
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import rumps

from src.config import Config, TRANSCRIPTION_PROMPT, SUMMARY_PROMPT, TITLE_PROMPT, APP_DIR, CONFIG_PATH, load_config, save_config
from src.model_selector import ModelSelector
from src.recorder import AudioRecorder
from src.transcriber import TranscriptionService, TranscriptionError
from src.notion_service import NotionService, save_transcription_locally
from src.settings_window import show_settings
from src.upload_window import show_upload

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


def _find_icons():
    """Resolve menu bar icons for both dev mode and py2app bundle."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent.parent / "Resources" / "media"
    else:
        base = Path(__file__).resolve().parent.parent / "media"
    return (
        str(base / "menu_icon.png"),
        str(base / "menu_icon_red.png"),
        str(base / "menu_icon_yellow.png"),
    )


def _generate_rotated_frames(icon_path: str, count: int = 8) -> list[str]:
    """Pre-generate rotated versions of the icon as temp files."""
    from AppKit import NSImage, NSBitmapImageRep, NSCompositeSourceOver, NSPNGFileType
    from Foundation import NSAffineTransform, NSMakeRect, NSSize

    src = NSImage.alloc().initByReferencingFile_(icon_path)
    w, h = int(src.size().width), int(src.size().height)

    paths = [icon_path]  # frame 0 is the original (0° rotation)
    tmpdir = tempfile.mkdtemp(prefix="open-transcribe-anim-")
    atexit.register(lambda d=tmpdir: __import__("shutil").rmtree(d, ignore_errors=True))
    for i in range(1, count):
        angle = (360 / count) * i
        rotated = NSImage.alloc().initWithSize_(NSSize(w, h))
        rotated.lockFocus()
        transform = NSAffineTransform.transform()
        transform.translateXBy_yBy_(w / 2, h / 2)
        transform.rotateByDegrees_(angle)
        transform.translateXBy_yBy_(-w / 2, -h / 2)
        transform.concat()
        src.drawInRect_fromRect_operation_fraction_(
            NSMakeRect(0, 0, w, h), NSMakeRect(0, 0, w, h), NSCompositeSourceOver, 1.0
        )
        rotated.unlockFocus()

        tiff = rotated.TIFFRepresentation()
        rep = NSBitmapImageRep.imageRepWithData_(tiff)
        data = rep.representationUsingType_properties_(NSPNGFileType, None)
        path = os.path.join(tmpdir, f"frame_{i}.png")
        data.writeToFile_atomically_(path, True)
        paths.append(path)

    return paths


ICON_PATH, ICON_REC_PATH, ICON_PAUSE_PATH = _find_icons()


class OpenTranscribeApp(rumps.App):
    def __init__(self):
        super().__init__("Open Transcribe", icon=ICON_PATH, quit_button=None)

        # Menu items stored as references for dynamic insertion/removal
        self._mi_start = rumps.MenuItem("Start Recording", callback=self._start_recording)
        self._mi_pause = rumps.MenuItem("Pause", callback=self._pause_recording)
        self._mi_continue = rumps.MenuItem("Continue", callback=self._resume_recording)
        self._mi_stop = rumps.MenuItem("Stop Recording", callback=self._stop_recording)
        self._mi_sep = None  # separator key assigned by rumps

        self._mi_upload = rumps.MenuItem("Upload a Recording", callback=self._open_upload)

        self.menu = [
            self._mi_start,
            self._mi_upload,
            None,  # separator
            rumps.MenuItem("Settings", callback=self.open_settings),
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

        self._recorder = AudioRecorder()
        self._transcriber: TranscriptionService | None = None
        self._notion: NotionService | None = None
        self._config: Config | None = None
        self._recording_start: datetime | None = None

        # Animated processing icon (lazy-generated on first use)
        self._anim_frames: list[str] | None = None
        self._anim_index = 0
        self._anim_timer = rumps.Timer(self._animate_icon, 0.15)

        self._load_and_init()

    def _ensure_anim_frames(self):
        """Lazy-generate rotated icon frames on first use."""
        if self._anim_frames is not None:
            return
        try:
            self._anim_frames = _generate_rotated_frames(ICON_PATH)
        except Exception as e:
            log.warning(f"Could not generate animation frames: {e}")
            self._anim_frames = []

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
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=config.gemini_api_key,
            http_options=types.HttpOptions(
                timeout=600_000,  # 10 min for long audio
                retry_options=types.HttpRetryOptions(
                    attempts=3,
                    initial_delay=1.0,
                    max_delay=30.0,
                    exp_base=2,
                    jitter=1.0,
                    http_status_codes=[408, 500, 502, 503, 504],
                ),
            ),
        )
        model_selector = ModelSelector(client, preferred_model=config.gemini_model)
        self._transcriber = TranscriptionService(client, model_selector)
        self._notion = NotionService(config.notion_token, config.notion_database_id)
        log.info("Services ready.")

    # ── Menu visibility helpers ────────────────────────────────────────

    _DYNAMIC_KEYS = ("Start Recording", "Pause", "Continue", "Stop Recording")

    def _clear_dynamic_menu(self):
        """Remove all recording-related menu items."""
        for key in self._DYNAMIC_KEYS:
            if key in self.menu:
                del self.menu[key]

    def _show_idle_menu(self):
        """Show only 'Start Recording'."""
        self._clear_dynamic_menu()
        self.menu.insert_before("Settings", self._mi_start)

    def _show_recording_menu(self):
        """Show 'Pause' and 'Stop Recording'."""
        self._clear_dynamic_menu()
        self.menu.insert_before("Settings", self._mi_pause)
        self.menu.insert_before("Settings", self._mi_stop)

    def _show_paused_menu(self):
        """Show 'Continue' and 'Stop Recording'."""
        self._clear_dynamic_menu()
        self.menu.insert_before("Settings", self._mi_continue)
        self.menu.insert_before("Settings", self._mi_stop)

    # ── Recording flow ───────────────────────────────────────────────

    def _start_recording(self, sender):
        # Reload config fresh every time the user starts recording
        self._load_and_init()

        if not self._transcriber:
            log.warning("Tried to record without config")
            rumps.notification("Open Transcribe", "Not configured", "Click Settings, add keys, then Reload Config.")
            return

        self._recording_start = datetime.now().astimezone()
        timestamp = self._recording_start.strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"meeting_{timestamp}.wav"

        try:
            filepath = self._recorder.start(filename)
            log.info(f"Recording started: {filepath} (device: {self._recorder.device_name})")
        except Exception as e:
            log.error(f"Recording failed to start: {e}")
            rumps.notification("Open Transcribe", "Recording failed", str(e))
            return

        self.icon = ICON_REC_PATH
        self.title = ""
        self._show_recording_menu()
        rumps.notification(
            "Open Transcribe",
            "Recording started",
            f"Device: {self._recorder.device_name}",
        )

    def _pause_recording(self, sender):
        self._recorder.pause()
        self.icon = ICON_PAUSE_PATH
        self._show_paused_menu()
        log.info("Recording paused")

    def _resume_recording(self, sender):
        self._recorder.resume()
        self.icon = ICON_REC_PATH
        self._show_recording_menu()
        log.info("Recording resumed")

    def _stop_recording(self, sender):
        audio_path = self._recorder.stop()
        recording_end = datetime.now().astimezone()
        self._show_idle_menu()
        log.info(f"Recording stopped: {audio_path}")

        if not audio_path:
            log.warning("No audio path returned")
            self.icon = ICON_PATH
            self.title = ""
            return

        # Start processing animation
        self._start_anim()

        duration = (recording_end - self._recording_start).total_seconds()
        log.info(f"Duration: {duration:.1f}s")
        date = self._recording_start

        # Process in background thread so UI stays responsive
        thread = threading.Thread(
            target=self._process_recording,
            args=(audio_path, date, duration),
            daemon=True,
        )
        thread.start()

    # ── Animated processing icon ─────────────────────────────────────

    def _start_anim(self):
        """Start the rotating icon animation."""
        self._ensure_anim_frames()
        self._anim_index = 0
        if self._anim_frames:
            self.icon = self._anim_frames[0]
            self._anim_timer.start()
        else:
            self.icon = ICON_PATH
            self.title = "⏳"

    def _stop_anim(self):
        """Stop animation and restore the idle icon."""
        self._anim_timer.stop()
        self.icon = ICON_PATH
        self.title = ""

    def _animate_icon(self, timer):
        """Timer callback — cycle through rotated icon frames."""
        self._anim_index = (self._anim_index + 1) % len(self._anim_frames)
        self.icon = self._anim_frames[self._anim_index]

    # ── Background processing ─────────────────────────────────────────

    def _process_recording(self, audio_path: str, date: datetime, duration: float):
        # Prevent macOS App Nap from suspending us during processing
        try:
            from Foundation import NSProcessInfo
            activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                0x00FFFFFF,  # NSActivityUserInitiatedAllowingIdleSystemSleep
                "Processing transcription",
            )
        except Exception:
            activity = None

        try:
            self._process_recording_inner(audio_path, date, duration)
        finally:
            if activity is not None:
                try:
                    NSProcessInfo.processInfo().endActivity_(activity)
                except Exception:
                    pass

    def _process_recording_inner(self, audio_path: str, date: datetime, duration: float, max_retries: int = 2):
        log.info(f"Transcribing {audio_path}...")
        for attempt in range(1, max_retries + 1):
            try:
                transcription, summary, title = self._transcriber.transcribe_and_summarize(
                    audio_path, TRANSCRIPTION_PROMPT, SUMMARY_PROMPT, TITLE_PROMPT
                )
                log.info(f"Transcription complete ({len(transcription)} chars), summary ({len(summary)} chars)")
                break
            except TranscriptionError as e:
                if attempt < max_retries:
                    log.warning(f"Transcription attempt {attempt} failed: {e}. Retrying...")
                    continue
                log.error(f"Transcription failed: {e}")
                rumps.notification("Open Transcribe", "Transcription failed", str(e))
                self._stop_anim()
                return

        # Save to Notion
        log.info("Saving to Notion...")
        try:
            url = self._notion.save_transcription(title, date, duration, transcription, summary)
            log.info(f"Saved to Notion: {url}")
            rumps.notification("Open Transcribe", "Saved to Notion", title)
        except Exception as e:
            log.error(f"Notion save failed: {e}")
            # Fallback: save locally
            local_path = save_transcription_locally(title, date, duration, transcription, summary)
            log.info(f"Saved locally: {local_path}")
            rumps.notification(
                "Open Transcribe",
                "Notion failed — saved locally",
                f"{local_path}\n{e}",
            )

        self._stop_anim()

    # ── Settings ──────────────────────────────────────────────────────

    def open_settings(self, sender):
        """Open the native settings window."""
        show_settings(
            self._config or Config(),
            on_save=self._apply_settings,
            gemini_client=self._transcriber._client if self._transcriber else None,
        )

    def _apply_settings(self, config: Config):
        """Save config to disk and reinitialize services."""
        save_config(config)
        self._load_and_init()

    # ── Upload ───────────────────────────────────────────────────────

    def _open_upload(self, sender):
        """Open the upload window for drag-and-drop transcription."""
        self._load_and_init()

        if not self._transcriber:
            log.warning("Tried to upload without config")
            rumps.notification("Open Transcribe", "Not configured", "Click Settings, add keys, then Reload Config.")
            return

        show_upload(on_drop=self._handle_uploaded_file)

    def _handle_uploaded_file(self, path: str):
        """Handle a file dropped onto the upload window."""
        import soundfile as sf

        date = datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
        try:
            info = sf.info(path)
            duration = info.duration
        except Exception:
            duration = 0.0
        self._start_anim()
        thread = threading.Thread(
            target=self._process_recording,
            args=(path, date, duration),
            daemon=True,
        )
        thread.start()


def main():
    _kill_previous_instance()
    _write_pid()
    if ".app/Contents" not in sys.executable:
        _ensure_info_plist()
    atexit.register(_cleanup_pid)

    log.info("Starting Open Transcribe...")
    app = OpenTranscribeApp()
    app.run()
