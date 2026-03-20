"""Tests for src/app.py.

rumps and AppKit/Foundation must be mocked in sys.modules before
importing src.app, because the module-level code uses them.
"""

import os
import signal
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

# ── Mock rumps at sys.modules level ──────────────────────────────────


def _build_mock_rumps():
    """Build a mock rumps module that satisfies import-time usage."""
    mock_rumps = MagicMock()

    class FakeApp:
        def __init__(self, name, icon=None, quit_button=None):
            self.name = name
            self.icon = icon
            self.title = ""
            self.menu = MagicMock()

        def run(self):
            pass

    mock_rumps.App = FakeApp
    mock_rumps.MenuItem = MagicMock
    mock_rumps.Timer = MagicMock
    mock_rumps.quit_application = MagicMock()
    mock_rumps.notification = MagicMock()
    return mock_rumps


@pytest.fixture(autouse=True)
def mock_rumps_module():
    """Ensure rumps is mocked in sys.modules for all tests in this file."""
    mock_rumps = _build_mock_rumps()
    old = sys.modules.get("rumps")
    sys.modules["rumps"] = mock_rumps
    yield mock_rumps
    if old is not None:
        sys.modules["rumps"] = old
    else:
        sys.modules.pop("rumps", None)


@pytest.fixture
def app_module(mock_rumps_module):
    """Import (or reimport) src.app with mocked rumps."""
    for mod_name in list(sys.modules):
        if mod_name == "src.app" or mod_name.startswith("src.app."):
            del sys.modules[mod_name]
    with patch.dict("os.environ", {}, clear=False):
        import src.app
    return src.app


# ── Shared helpers ────────────────────────────────────────────────────


def _make_fake_import(foundation_mock=None):
    """Build a fake __import__ that intercepts Foundation/AppKit imports.

    Args:
        foundation_mock: If None, importing Foundation raises ImportError.
                        Otherwise, a mock module whose NSProcessInfo is used.
    """

    def fake_import(name, *args, **kwargs):
        if name == "Foundation":
            if foundation_mock is None:
                raise ImportError("no Foundation")
            return foundation_mock
        if name == "AppKit":
            return MagicMock()
        return __import__(name, *args, **kwargs)

    return fake_import


# ── Module-level functions ────────────────────────────────────────────


class TestEnsureInfoPlist:
    def test_calls_plistbuddy(self, app_module):
        with patch("src.app.subprocess.run") as mock_run:
            app_module._ensure_info_plist()
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert "/usr/libexec/PlistBuddy" in args[0][0]
        assert args[1]["check"] is False


class TestKillPreviousInstance:
    def test_different_pid_sends_sigterm(self, app_module, tmp_path):
        pid_file = tmp_path / "app.pid"
        pid_file.write_text("99999")
        with (
            patch.object(app_module, "PID_FILE", pid_file),
            patch("src.app.os.getpid", return_value=12345),
            patch("src.app.os.kill") as mock_kill,
        ):
            app_module._kill_previous_instance()
        mock_kill.assert_called_once_with(99999, signal.SIGTERM)

    def test_same_pid_skips(self, app_module, tmp_path):
        pid_file = tmp_path / "app.pid"
        pid_file.write_text("12345")
        with (
            patch.object(app_module, "PID_FILE", pid_file),
            patch("src.app.os.getpid", return_value=12345),
            patch("src.app.os.kill") as mock_kill,
        ):
            app_module._kill_previous_instance()
        mock_kill.assert_not_called()

    def test_missing_file(self, app_module, tmp_path):
        pid_file = tmp_path / "nonexistent.pid"
        with patch.object(app_module, "PID_FILE", pid_file):
            app_module._kill_previous_instance()

    def test_corrupt_file(self, app_module, tmp_path):
        pid_file = tmp_path / "app.pid"
        pid_file.write_text("not-a-number")
        with patch.object(app_module, "PID_FILE", pid_file):
            app_module._kill_previous_instance()
        assert not pid_file.exists()

    def test_process_lookup_error(self, app_module, tmp_path):
        pid_file = tmp_path / "app.pid"
        pid_file.write_text("99999")
        with (
            patch.object(app_module, "PID_FILE", pid_file),
            patch("src.app.os.getpid", return_value=12345),
            patch("src.app.os.kill", side_effect=ProcessLookupError),
        ):
            app_module._kill_previous_instance()


class TestWritePid:
    def test_writes_pid(self, app_module, tmp_path):
        pid_file = tmp_path / "app.pid"
        with (
            patch.object(app_module, "PID_FILE", pid_file),
            patch.object(app_module, "APP_DIR", tmp_path),
        ):
            app_module._write_pid()
        assert pid_file.exists()
        assert int(pid_file.read_text()) == os.getpid()


class TestCleanupPid:
    def test_removes_pid(self, app_module, tmp_path):
        pid_file = tmp_path / "app.pid"
        pid_file.write_text("123")
        with patch.object(app_module, "PID_FILE", pid_file):
            app_module._cleanup_pid()
        assert not pid_file.exists()


class TestFindIcons:
    def test_dev_mode(self, app_module):
        with patch.object(sys, "frozen", False, create=True):
            icons = app_module._find_icons()
        assert len(icons) == 3
        assert "menu_icon.png" in icons[0]
        assert "menu_icon_red.png" in icons[1]
        assert "menu_icon_yellow.png" in icons[2]

    def test_frozen_mode(self, app_module):
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", "/Applications/App.app/Contents/MacOS/python"),
        ):
            icons = app_module._find_icons()
        assert len(icons) == 3
        assert "Resources/media/menu_icon.png" in icons[0]


class TestGenerateRotatedFrames:
    def test_correct_frame_count(self, app_module):
        mock_appkit = MagicMock()
        mock_foundation = MagicMock()
        mock_img = MagicMock()
        mock_img.size.return_value.width = 22
        mock_img.size.return_value.height = 22
        mock_appkit.NSImage.alloc.return_value.initByReferencingFile_.return_value = mock_img
        mock_appkit.NSImage.alloc.return_value.initWithSize_.return_value = MagicMock()

        def fake_import(name, *args, **kwargs):
            if name == "AppKit":
                return mock_appkit
            if name == "Foundation":
                return mock_foundation
            return __import__(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=fake_import),
            patch("tempfile.mkdtemp", return_value="/tmp/test-anim"),
            patch("atexit.register"),
        ):
            frames = app_module._generate_rotated_frames("/icon.png", count=4)

        assert frames[0] == "/icon.png"
        assert len(frames) == 4


# ── OpenTranscribeApp class ───────────────────────────────────────────


@pytest.fixture
def app(app_module):
    """Create an OpenTranscribeApp instance with mocked dependencies."""
    with (
        patch.object(app_module, "load_config", return_value=(None, ["gemini_api_key"])),
        patch.object(app_module, "AudioRecorder"),
        patch.object(app_module, "rumps") as mock_rumps_ref,
    ):
        mock_rumps_ref.Timer = MagicMock
        mock_rumps_ref.MenuItem = MagicMock
        mock_rumps_ref.quit_application = MagicMock()
        instance = app_module.OpenTranscribeApp()
    instance._recorder = MagicMock()
    return instance


class TestAppInit:
    def test_has_menu_items(self, app):
        assert app._mi_start is not None
        assert app._mi_pause is not None
        assert app._mi_continue is not None
        assert app._mi_stop is not None

    def test_has_recorder(self, app):
        assert app._recorder is not None

    def test_animation_state(self, app):
        assert app._anim_frames is None
        assert app._anim_index == 0


class TestLoadAndInit:
    def test_valid_config_inits_services(self, app_module):
        config = MagicMock()
        config.gemini_api_key = "test-key"
        config.gemini_model = ""
        config.notion_token = "test-token"
        config.notion_database_id = "test-db"
        with (
            patch.object(app_module, "load_config", return_value=(config, [])),
            patch.object(app_module, "AudioRecorder"),
            patch.object(app_module, "rumps"),
            patch.object(app_module, "ModelSelector"),
            patch("google.genai.Client") as _mock_client_cls,
        ):
            instance = app_module.OpenTranscribeApp()
        assert instance._transcriber is not None
        assert instance._notion is not None

    def test_missing_keys_no_services(self, app_module):
        config = MagicMock()
        with (
            patch.object(app_module, "load_config", return_value=(config, ["gemini_api_key"])),
            patch.object(app_module, "AudioRecorder"),
            patch.object(app_module, "rumps"),
        ):
            instance = app_module.OpenTranscribeApp()
        assert instance._transcriber is None
        assert instance._notion is None

    def test_no_config_uses_default(self, app_module):
        with (
            patch.object(app_module, "load_config", return_value=(None, ["gemini_api_key"])),
            patch.object(app_module, "AudioRecorder"),
            patch.object(app_module, "rumps"),
        ):
            instance = app_module.OpenTranscribeApp()
        assert instance._config is not None


# ── Menu transitions ──────────────────────────────────────────────────


class TestMenuTransitions:
    def test_clear_dynamic_menu(self, app):
        menu_dict = {
            "Start Recording": True,
            "Pause": True,
            "Continue": True,
            "Stop Recording": True,
        }
        app.menu = MagicMock()
        app.menu.__contains__ = lambda self, key: key in menu_dict
        app.menu.__delitem__ = lambda self, key: menu_dict.pop(key, None)
        app._clear_dynamic_menu()
        assert len(menu_dict) == 0

    def test_show_idle_menu(self, app):
        app.menu = MagicMock()
        app._show_idle_menu()
        app.menu.insert_before.assert_called()

    def test_show_recording_menu(self, app):
        app.menu = MagicMock()
        app._show_recording_menu()
        calls = app.menu.insert_before.call_args_list
        assert len(calls) == 2

    def test_show_paused_menu(self, app):
        app.menu = MagicMock()
        app._show_paused_menu()
        calls = app.menu.insert_before.call_args_list
        assert len(calls) == 2


# ── Recording flow ────────────────────────────────────────────────────


class TestStartRecording:
    def test_success(self, app, app_module):
        app._transcriber = MagicMock()
        app._recorder.start.return_value = "/tmp/meeting.wav"
        app._recorder.device_name = "BlackHole"
        with (
            patch.object(app, "_load_and_init"),
            patch.object(app, "_show_recording_menu"),
            patch.object(app_module, "rumps"),
        ):
            app._start_recording(None)
        assert app._recording_start is not None
        assert app.icon == app_module.ICON_REC_PATH

    def test_no_transcriber(self, app, app_module):
        app._transcriber = None
        with (
            patch.object(app, "_load_and_init"),
            patch.object(app_module, "rumps") as mock_r,
        ):
            app._start_recording(None)
        mock_r.notification.assert_called()

    def test_recorder_error(self, app, app_module):
        app._transcriber = MagicMock()
        app._recorder.start.side_effect = RuntimeError("device error")
        with (
            patch.object(app, "_load_and_init"),
            patch.object(app_module, "rumps") as mock_r,
        ):
            app._start_recording(None)
        mock_r.notification.assert_called()

    def test_reloads_config(self, app, app_module):
        app._transcriber = None
        with (
            patch.object(app, "_load_and_init") as mock_load,
            patch.object(app_module, "rumps"),
        ):
            app._start_recording(None)
        mock_load.assert_called_once()


class TestPauseRecording:
    def test_pause(self, app, app_module):
        with patch.object(app, "_show_paused_menu"):
            app._pause_recording(None)
        app._recorder.pause.assert_called_once()
        assert app.icon == app_module.ICON_PAUSE_PATH

    def test_resume(self, app, app_module):
        with patch.object(app, "_show_recording_menu"):
            app._resume_recording(None)
        app._recorder.resume.assert_called_once()
        assert app.icon == app_module.ICON_REC_PATH


class TestStopRecording:
    def test_with_audio(self, app, app_module):
        app._recorder.stop.return_value = "/tmp/meeting.wav"
        app._recording_start = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
        with (
            patch.object(app, "_show_idle_menu"),
            patch.object(app, "_start_anim"),
            patch("src.app.threading.Thread") as mock_thread_cls,
            patch("src.app.datetime") as mock_dt,
        ):
            mock_dt.now.return_value = datetime(2024, 1, 1, 10, 1, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            app._stop_recording(None)
        mock_thread_cls.assert_called_once()
        mock_thread_cls.return_value.start.assert_called_once()

    def test_without_audio(self, app, app_module):
        app._recorder.stop.return_value = None
        with (
            patch.object(app, "_show_idle_menu"),
            patch.object(app, "_start_anim") as mock_anim,
        ):
            app._stop_recording(None)
        mock_anim.assert_not_called()
        assert app.icon == app_module.ICON_PATH


# ── Animation ─────────────────────────────────────────────────────────


class TestEnsureAnimFrames:
    def test_lazy_init(self, app, app_module):
        app._anim_frames = None
        with patch.object(app_module, "_generate_rotated_frames", return_value=["f0", "f1"]):
            app._ensure_anim_frames()
        assert app._anim_frames == ["f0", "f1"]

    def test_error_gives_empty_list(self, app, app_module):
        app._anim_frames = None
        with patch.object(app_module, "_generate_rotated_frames", side_effect=RuntimeError("fail")):
            app._ensure_anim_frames()
        assert app._anim_frames == []

    def test_already_generated_noop(self, app, app_module):
        app._anim_frames = ["existing"]
        with patch.object(app_module, "_generate_rotated_frames") as mock_gen:
            app._ensure_anim_frames()
        mock_gen.assert_not_called()


class TestStartAnim:
    def test_with_frames(self, app, app_module):
        app._anim_frames = None
        with patch.object(
            app_module,
            "_generate_rotated_frames",
            return_value=["/icon.png", "/f1.png"],
        ):
            app._ensure_anim_frames()
        app._anim_timer = MagicMock()
        app._start_anim()
        assert app.icon == "/icon.png"
        app._anim_timer.start.assert_called_once()

    def test_without_frames(self, app, app_module):
        app._anim_frames = None
        with patch.object(app_module, "_generate_rotated_frames", return_value=[]):
            app._ensure_anim_frames()
        app._anim_timer = MagicMock()
        app._start_anim()
        assert app.icon == app_module.ICON_PATH
        assert app.title == "\u23f3"


class TestStopAnim:
    def test_resets_state(self, app, app_module):
        app._anim_timer = MagicMock()
        app._stop_anim()
        app._anim_timer.stop.assert_called_once()
        assert app.icon == app_module.ICON_PATH
        assert app.title == ""


class TestAnimateIcon:
    def test_cycles_frames(self, app):
        app._anim_frames = ["/f0.png", "/f1.png", "/f2.png"]
        app._anim_index = 0
        app._animate_icon(None)
        assert app._anim_index == 1
        assert app.icon == "/f1.png"
        app._animate_icon(None)
        assert app._anim_index == 2
        app._animate_icon(None)
        assert app._anim_index == 0


# ── Background processing ────────────────────────────────────────────


class TestProcessRecording:
    def test_activity_acquired_and_released(self, app, app_module):
        mock_nsprocessinfo = MagicMock()
        mock_activity = MagicMock()
        mock_nsprocessinfo.processInfo.return_value.beginActivityWithOptions_reason_.return_value = mock_activity
        foundation_mod = MagicMock()
        foundation_mod.NSProcessInfo = mock_nsprocessinfo

        with (
            patch("builtins.__import__", side_effect=_make_fake_import(foundation_mod)),
            patch.object(app, "_process_recording_inner"),
        ):
            app._process_recording("/audio.wav", datetime.now(), 60.0)

        mock_nsprocessinfo.processInfo.return_value.endActivity_.assert_called_once_with(mock_activity)

    def test_import_error_handled(self, app, app_module):
        with (
            patch("builtins.__import__", side_effect=_make_fake_import(None)),
            patch.object(app, "_process_recording_inner"),
        ):
            app._process_recording("/audio.wav", datetime.now(), 60.0)

    def test_end_activity_error_swallowed(self, app, app_module):
        mock_nsprocessinfo = MagicMock()
        mock_activity = MagicMock()
        mock_nsprocessinfo.processInfo.return_value.beginActivityWithOptions_reason_.return_value = mock_activity
        mock_nsprocessinfo.processInfo.return_value.endActivity_.side_effect = RuntimeError("end failed")
        foundation_mod = MagicMock()
        foundation_mod.NSProcessInfo = mock_nsprocessinfo

        with (
            patch("builtins.__import__", side_effect=_make_fake_import(foundation_mod)),
            patch.object(app, "_process_recording_inner"),
        ):
            app._process_recording("/audio.wav", datetime.now(), 60.0)


class TestProcessRecordingInner:
    def test_success(self, app, app_module):
        app._transcriber = MagicMock()
        app._transcriber.transcribe_and_summarize.return_value = (
            "trans",
            "sum",
            "title",
        )
        app._notion = MagicMock()
        app._notion.save_transcription.return_value = "https://notion.so/page"
        with (
            patch.object(app, "_stop_anim"),
            patch.object(app_module, "rumps"),
        ):
            app._process_recording_inner("/audio.wav", datetime.now(), 60.0)
        app._notion.save_transcription.assert_called_once()

    def test_retry_on_failure(self, app, app_module):
        app._transcriber = MagicMock()
        from src.transcriber import TranscriptionError

        app._transcriber.transcribe_and_summarize.side_effect = [
            TranscriptionError("fail"),
            ("trans", "sum", "title"),
        ]
        app._notion = MagicMock()
        app._notion.save_transcription.return_value = "url"
        with (
            patch.object(app, "_stop_anim"),
            patch.object(app_module, "rumps"),
        ):
            app._process_recording_inner("/audio.wav", datetime.now(), 60.0)
        assert app._transcriber.transcribe_and_summarize.call_count == 2

    def test_all_retries_exhausted(self, app, app_module):
        app._transcriber = MagicMock()
        from src.transcriber import TranscriptionError

        app._transcriber.transcribe_and_summarize.side_effect = TranscriptionError("fail")
        with (
            patch.object(app, "_stop_anim"),
            patch.object(app_module, "rumps") as mock_r,
        ):
            app._process_recording_inner("/audio.wav", datetime.now(), 60.0)
        mock_r.notification.assert_called()

    def test_notion_failure_local_fallback(self, app, app_module):
        app._transcriber = MagicMock()
        app._transcriber.transcribe_and_summarize.return_value = (
            "trans",
            "sum",
            "title",
        )
        app._notion = MagicMock()
        app._notion.save_transcription.side_effect = RuntimeError("notion down")
        with (
            patch.object(app, "_stop_anim"),
            patch.object(app_module, "rumps"),
            patch.object(app_module, "save_transcription_locally", return_value="/local/path") as mock_local,
        ):
            app._process_recording_inner("/audio.wav", datetime.now(), 60.0)
        mock_local.assert_called_once()


# ── Settings ──────────────────────────────────────────────────────────


class TestOpenSettings:
    def test_calls_show_settings(self, app, app_module):
        app._config = MagicMock()
        app._transcriber = MagicMock()
        app._transcriber._client = MagicMock()
        with patch.object(app_module, "show_settings") as mock_show:
            app.open_settings(None)
        mock_show.assert_called_once_with(
            app._config,
            on_save=app._apply_settings,
            gemini_client=app._transcriber._client,
        )

    def test_open_settings_with_no_config(self, app, app_module):
        app._config = None
        app._transcriber = MagicMock()
        app._transcriber._client = MagicMock()
        with patch.object(app_module, "show_settings") as mock_show:
            app.open_settings(None)
        passed_config = mock_show.call_args[0][0]
        assert isinstance(passed_config, app_module.Config)

    def test_open_settings_without_transcriber(self, app, app_module):
        app._config = MagicMock()
        app._transcriber = None
        with patch.object(app_module, "show_settings") as mock_show:
            app.open_settings(None)
        mock_show.assert_called_once_with(
            app._config,
            on_save=app._apply_settings,
            gemini_client=None,
        )

    def test_apply_settings_saves_and_reloads(self, app, app_module):
        config = MagicMock()
        with (
            patch.object(app_module, "save_config") as mock_save,
            patch.object(app, "_load_and_init") as mock_load,
        ):
            app._apply_settings(config)
        mock_save.assert_called_once_with(config)
        mock_load.assert_called_once()


# ── main() ────────────────────────────────────────────────────────────


@pytest.fixture
def main_mocks(app_module):
    """Shared patch stanza for main() tests."""
    with (
        patch.object(app_module, "_kill_previous_instance") as mock_kill,
        patch.object(app_module, "_write_pid") as mock_write,
        patch.object(app_module, "_ensure_info_plist") as mock_plist,
        patch.object(app_module, "_cleanup_pid") as mock_cleanup,
        patch("src.app.atexit.register") as mock_atexit,
        patch.object(app_module, "OpenTranscribeApp") as mock_app_cls,
    ):
        yield {
            "kill": mock_kill,
            "write_pid": mock_write,
            "plist": mock_plist,
            "cleanup": mock_cleanup,
            "atexit": mock_atexit,
            "app_cls": mock_app_cls,
        }


class TestMain:
    def test_pid_lifecycle(self, app_module, main_mocks):
        with patch.object(sys, "executable", "/usr/local/bin/python"):
            app_module.main()
        main_mocks["kill"].assert_called_once()
        main_mocks["write_pid"].assert_called_once()
        main_mocks["atexit"].assert_called_once()
        main_mocks["app_cls"].return_value.run.assert_called_once()

    def test_plist_skipped_in_app_bundle(self, app_module, main_mocks):
        with patch.object(sys, "executable", "/App.app/Contents/MacOS/python"):
            app_module.main()
        main_mocks["plist"].assert_not_called()

    def test_atexit_registered(self, app_module, main_mocks):
        with patch.object(sys, "executable", "/usr/local/bin/python"):
            app_module.main()
        main_mocks["atexit"].assert_called_once_with(main_mocks["cleanup"])


# ── Upload recording ──────────────────────────────────────────────────


class TestUploadRecording:
    def test_open_upload_calls_show_upload(self, app, app_module):
        app._transcriber = MagicMock()
        with (
            patch.object(app, "_load_and_init"),
            patch.object(app_module, "show_upload") as mock_show,
        ):
            app._open_upload(None)
        mock_show.assert_called_once_with(on_drop=app._handle_uploaded_file)

    def test_open_upload_no_transcriber(self, app, app_module):
        app._transcriber = None
        with (
            patch.object(app, "_load_and_init"),
            patch.object(app_module, "rumps") as mock_r,
            patch.object(app_module, "show_upload") as mock_show,
        ):
            app._open_upload(None)
        mock_r.notification.assert_called()
        mock_show.assert_not_called()

    def test_open_upload_reloads_config(self, app, app_module):
        app._transcriber = None
        with (
            patch.object(app, "_load_and_init") as mock_load,
            patch.object(app_module, "rumps"),
        ):
            app._open_upload(None)
        mock_load.assert_called_once()

    def test_handle_uploaded_file(self, app, app_module):
        mock_info = MagicMock()
        mock_info.duration = 42.5
        with (
            patch.object(app, "_start_anim") as mock_anim,
            patch("src.app.threading.Thread") as mock_thread_cls,
            patch("src.app.os.path.getmtime", return_value=1700000000.0),
            patch.dict(
                "sys.modules",
                {"soundfile": MagicMock(**{"info.return_value": mock_info})},
            ),
        ):
            app._handle_uploaded_file("/tmp/audio.wav")
        mock_anim.assert_called_once()
        mock_thread_cls.assert_called_once()
        args = mock_thread_cls.call_args
        assert args[1]["target"] == app._process_recording
        assert args[1]["args"][0] == "/tmp/audio.wav"
        assert args[1]["args"][2] == 42.5
        mock_thread_cls.return_value.start.assert_called_once()

    def test_handle_uploaded_file_duration_fallback(self, app, app_module):
        mock_sf = MagicMock()
        mock_sf.info.side_effect = RuntimeError("unsupported format")
        with (
            patch.object(app, "_start_anim"),
            patch("src.app.threading.Thread") as mock_thread_cls,
            patch("src.app.os.path.getmtime", return_value=1700000000.0),
            patch.dict("sys.modules", {"soundfile": mock_sf}),
        ):
            app._handle_uploaded_file("/tmp/audio.mp3")
        args = mock_thread_cls.call_args
        assert args[1]["args"][2] == 0.0
