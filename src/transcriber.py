import logging
import os
import time
from pathlib import Path

from google import genai
from google.genai import types

from src.model_selector import ModelSelector, is_rate_limit_error

log = logging.getLogger("open-transcribe")


class TranscriptionError(Exception):
    pass


class TranscriptionService:
    def __init__(self, client: genai.Client, model_selector: ModelSelector):
        self._client = client
        self._model_selector = model_selector

    def transcribe_and_summarize(
        self,
        audio_path: str,
        transcription_prompt: str,
        summary_prompt: str,
        title_prompt: str,
    ) -> tuple[str, str, str]:
        """Transcribe audio, summarize, and generate title with automatic model fallback."""
        self._model_selector.reset()

        while True:
            model = self._model_selector.current_model
            try:
                log.info(f"Using model: {model}")
                transcription = self._transcribe_audio(audio_path, transcription_prompt, model)
                log.info(f"Transcription complete ({len(transcription)} chars)")

                summary = self._summarize(transcription, summary_prompt, model)
                log.info(f"Summary complete ({len(summary)} chars)")

                title = self._generate_title(transcription, title_prompt, model)
                log.info(f"Generated title: {title}")

                return transcription, summary, title
            except TranscriptionError:
                raise
            except Exception as e:
                if is_rate_limit_error(e) and self._model_selector.advance_on_rate_limit():
                    continue
                raise TranscriptionError(f"Transcription failed: {e}") from e

    def _transcribe_audio(self, audio_path: str, prompt: str, model: str) -> str:
        """Call 1: Send audio + transcription prompt, get verbatim text."""
        file_size = os.path.getsize(audio_path)
        threshold = 20 * 1024 * 1024  # 20 MB
        log.info(f"Audio file size: {file_size / 1024 / 1024:.1f} MB")

        if file_size < threshold:
            return self._transcribe_inline(audio_path, prompt, model)
        else:
            return self._transcribe_upload(audio_path, prompt, model)

    def _transcribe_inline(self, audio_path: str, prompt: str, model: str) -> str:
        log.info("Using inline upload (< 20 MB)")
        audio_bytes = Path(audio_path).read_bytes()
        response = self._client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                prompt,
            ],
        )
        return response.text

    def _transcribe_upload(self, audio_path: str, prompt: str, model: str) -> str:
        log.info("Using Files API upload (>= 20 MB)...")
        uploaded = self._client.files.upload(file=audio_path)
        log.info(f"Upload complete: {uploaded.name}. Waiting for processing...")

        try:
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
                model=model,
                contents=[uploaded, prompt],
            )
            return response.text
        finally:
            try:
                self._client.files.delete(name=uploaded.name)
                log.info(f"Cleaned up uploaded file: {uploaded.name}")
            except Exception:
                pass

    def _summarize(self, transcription_text: str, summary_prompt: str, model: str) -> str:
        """Call 2: Send transcription text + summary prompt, get summary."""
        log.info("Generating summary from transcription...")
        response = self._client.models.generate_content(
            model=model,
            contents=[summary_prompt + transcription_text],
        )
        return response.text

    def _generate_title(self, transcription_text: str, title_prompt: str, model: str) -> str:
        """Call 3: Generate a short title from the transcription."""
        log.info("Generating title from transcription...")
        response = self._client.models.generate_content(
            model=model,
            contents=[title_prompt + transcription_text[:4000]],
        )
        return response.text.strip()
