import sounddevice as sd
import soundfile as sf

from src.config import RECORDINGS_DIR


def find_blackhole_device() -> dict | None:
    """Find the BlackHole virtual audio device (case-insensitive)."""
    for device in sd.query_devices():
        if "blackhole" in device["name"].lower() and device["max_input_channels"] > 0:
            return device
    return None


class AudioRecorder:
    def __init__(self):
        self._stream: sd.InputStream | None = None
        self._file: sf.SoundFile | None = None
        self._filepath: str | None = None
        self.is_recording = False
        self.is_paused = False
        self.device_name: str = ""

    def start(self, filename: str) -> str:
        """Start recording. Returns the output file path."""
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        self._filepath = str(RECORDINGS_DIR / filename)

        device_info = find_blackhole_device()
        if device_info:
            device_index = device_info["index"]
            self.device_name = device_info["name"]
            samplerate = int(device_info["default_samplerate"])
        else:
            device_index = None  # default input
            default = sd.query_devices(kind="input")
            self.device_name = default["name"]
            samplerate = int(default["default_samplerate"])

        self._file = sf.SoundFile(
            self._filepath,
            mode="w",
            samplerate=samplerate,
            channels=1,
            subtype="PCM_16",
        )

        self._stream = sd.InputStream(
            device=device_index,
            channels=1,
            samplerate=samplerate,
            callback=self._audio_callback,
        )
        self._stream.start()
        self.is_recording = True
        return self._filepath

    def _audio_callback(self, indata, frames, time, status):
        if self._file is not None and not self.is_paused:
            self._file.write(indata.copy())

    def pause(self):
        """Pause recording — stream keeps running but audio is discarded."""
        self.is_paused = True

    def resume(self):
        """Resume recording after a pause."""
        self.is_paused = False

    def stop(self) -> str | None:
        """Stop recording. Returns the file path of the recording."""
        self.is_recording = False
        self.is_paused = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._file is not None:
            self._file.close()
            self._file = None
        path = self._filepath
        self._filepath = None
        return path
