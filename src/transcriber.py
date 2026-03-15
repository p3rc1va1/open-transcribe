import logging
import os
import time
from pathlib import Path

from google import genai
from google.genai import types

log = logging.getLogger("open-transcribe")


class TranscriptionError(Exception):
    pass


class TranscriptionService:
    def __init__(self, api_key: str):
        self._client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 600_000},  # 10 min for long audio
        )

    def transcribe(self, audio_path: str, prompt: str) -> str:
        """Transcribe audio file using Gemini. Routes by file size."""
        file_size = os.path.getsize(audio_path)
        threshold = 20 * 1024 * 1024  # 20 MB
        log.info(f"Audio file size: {file_size / 1024 / 1024:.1f} MB")

        try:
            if file_size < threshold:
                return self._transcribe_inline(audio_path, prompt)
            else:
                return self._transcribe_upload(audio_path, prompt)
        except TranscriptionError:
            raise
        except Exception as e:
            raise TranscriptionError(f"Transcription failed: {e}") from e

    def _transcribe_inline(self, audio_path: str, prompt: str) -> str:
        log.info("Using inline upload (< 20 MB)")
        audio_bytes = Path(audio_path).read_bytes()
        response = self._client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                prompt,
            ],
        )
        return response.text

    def _transcribe_upload(self, audio_path: str, prompt: str) -> str:
        log.info("Using Files API upload (>= 20 MB)...")
        uploaded = self._client.files.upload(file=audio_path)
        log.info(f"Upload complete: {uploaded.name}. Waiting for processing...")

        try:
            # Wait for file to become active (timeout after 10 min)
            max_polls = 300  # 300 * 2s = 10 min
            for _ in range(max_polls):
                if uploaded.state.name != "PROCESSING":
                    break
                time.sleep(2)
                uploaded = self._client.files.get(name=uploaded.name)
            else:
                raise TranscriptionError("Gemini file processing timed out")

            if uploaded.state.name == "FAILED":
                raise TranscriptionError("Gemini file processing failed")

            log.info("File ready. Generating transcription...")
            response = self._client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[uploaded, prompt],
            )
            return response.text
        finally:
            # Clean up uploaded file from Gemini storage
            try:
                self._client.files.delete(name=uploaded.name)
                log.info(f"Cleaned up uploaded file: {uploaded.name}")
            except Exception:
                pass
