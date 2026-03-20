from unittest.mock import MagicMock, patch

import numpy as np

from src.recorder import AudioRecorder, find_blackhole_device

# ── find_blackhole_device ─────────────────────────────────────────────


class TestFindBlackholeDevice:
    def test_found(self):
        devices = [
            {"name": "BlackHole 2ch", "max_input_channels": 2, "index": 3},
            {"name": "Built-in Mic", "max_input_channels": 1, "index": 0},
        ]
        with patch("src.recorder.sd.query_devices", return_value=devices):
            result = find_blackhole_device()
        assert result == devices[0]

    def test_case_insensitive(self):
        devices = [
            {"name": "BLACKHOLE 16ch", "max_input_channels": 16, "index": 5},
        ]
        with patch("src.recorder.sd.query_devices", return_value=devices):
            result = find_blackhole_device()
        assert result is not None
        assert result["name"] == "BLACKHOLE 16ch"

    def test_not_found_empty_list(self):
        with patch("src.recorder.sd.query_devices", return_value=[]):
            assert find_blackhole_device() is None

    def test_not_found_other_devices(self):
        devices = [{"name": "Built-in Mic", "max_input_channels": 1, "index": 0}]
        with patch("src.recorder.sd.query_devices", return_value=devices):
            assert find_blackhole_device() is None

    def test_not_found_zero_input_channels(self):
        devices = [{"name": "BlackHole 2ch", "max_input_channels": 0, "index": 1}]
        with patch("src.recorder.sd.query_devices", return_value=devices):
            assert find_blackhole_device() is None


# ── AudioRecorder.__init__ ────────────────────────────────────────────


class TestAudioRecorderInit:
    def test_defaults(self):
        rec = AudioRecorder()
        assert rec._stream is None
        assert rec._file is None
        assert rec._filepath is None
        assert rec.is_recording is False
        assert rec.is_paused is False
        assert rec.device_name == ""


# ── AudioRecorder.start ──────────────────────────────────────────────


class TestAudioRecorderStart:
    def test_with_blackhole(self, tmp_path):
        bh = {
            "name": "BlackHole 2ch",
            "max_input_channels": 2,
            "index": 3,
            "default_samplerate": 48000.0,
        }
        mock_stream = MagicMock()
        with (
            patch("src.recorder.RECORDINGS_DIR", tmp_path),
            patch("src.recorder.find_blackhole_device", return_value=bh),
            patch("src.recorder.sf.SoundFile"),
            patch("src.recorder.sd.InputStream", return_value=mock_stream),
        ):
            rec = AudioRecorder()
            path = rec.start("test.wav")
        assert path == str(tmp_path / "test.wav")
        assert rec.device_name == "BlackHole 2ch"
        assert rec.is_recording is True
        mock_stream.start.assert_called_once()

    def test_without_blackhole(self, tmp_path):
        default_dev = {"name": "Built-in Mic", "default_samplerate": 44100.0}
        mock_stream = MagicMock()
        with (
            patch("src.recorder.RECORDINGS_DIR", tmp_path),
            patch("src.recorder.find_blackhole_device", return_value=None),
            patch("src.recorder.sd.query_devices", return_value=default_dev),
            patch("src.recorder.sf.SoundFile"),
            patch("src.recorder.sd.InputStream", return_value=mock_stream),
        ):
            rec = AudioRecorder()
            rec.start("test.wav")
        assert rec.device_name == "Built-in Mic"

    def test_creates_recordings_dir(self, tmp_path):
        rec_dir = tmp_path / "sub" / "recordings"
        bh = {
            "name": "BlackHole",
            "max_input_channels": 2,
            "index": 0,
            "default_samplerate": 48000.0,
        }
        with (
            patch("src.recorder.RECORDINGS_DIR", rec_dir),
            patch("src.recorder.find_blackhole_device", return_value=bh),
            patch("src.recorder.sf.SoundFile"),
            patch("src.recorder.sd.InputStream", return_value=MagicMock()),
        ):
            rec = AudioRecorder()
            rec.start("test.wav")
        assert rec_dir.exists()


# ── _audio_callback ──────────────────────────────────────────────────


class TestAudioCallback:
    def test_writes_when_not_paused(self):
        rec = AudioRecorder()
        rec._file = MagicMock()
        rec.is_paused = False
        data = np.zeros((1024, 1))
        rec._audio_callback(data, 1024, None, None)
        rec._file.write.assert_called_once()

    def test_skips_when_paused(self):
        rec = AudioRecorder()
        rec._file = MagicMock()
        rec.is_paused = True
        rec._audio_callback(np.zeros((1024, 1)), 1024, None, None)
        rec._file.write.assert_not_called()

    def test_skips_when_file_is_none(self):
        rec = AudioRecorder()
        rec._file = None
        rec.is_paused = False
        # Should not raise
        rec._audio_callback(np.zeros((1024, 1)), 1024, None, None)


# ── pause / resume ────────────────────────────────────────────────────


class TestPauseResume:
    def test_pause(self):
        rec = AudioRecorder()
        rec.pause()
        assert rec.is_paused is True

    def test_resume(self):
        rec = AudioRecorder()
        rec.is_paused = True
        rec.resume()
        assert rec.is_paused is False


# ── stop ──────────────────────────────────────────────────────────────


class TestStop:
    def test_closes_stream_and_file(self):
        rec = AudioRecorder()
        mock_stream = MagicMock()
        mock_file = MagicMock()
        rec._stream = mock_stream
        rec._file = mock_file
        rec._filepath = "/tmp/test.wav"
        rec.is_recording = True
        path = rec.stop()
        assert path == "/tmp/test.wav"
        assert rec.is_recording is False
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()
        mock_file.close.assert_called_once()
        assert rec._stream is None
        assert rec._file is None
        assert rec._filepath is None

    def test_handles_none_stream(self):
        rec = AudioRecorder()
        rec._stream = None
        rec._file = MagicMock()
        rec._filepath = "/tmp/test.wav"
        path = rec.stop()
        assert path == "/tmp/test.wav"

    def test_handles_none_file(self):
        rec = AudioRecorder()
        rec._stream = MagicMock()
        rec._file = None
        rec._filepath = "/tmp/test.wav"
        path = rec.stop()
        assert path == "/tmp/test.wav"

    def test_returns_none_when_no_filepath(self):
        rec = AudioRecorder()
        assert rec.stop() is None
