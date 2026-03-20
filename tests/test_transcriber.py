from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from src.transcriber import TranscriptionError, TranscriptionService


@pytest.fixture
def transcription_svc():
    """Create a TranscriptionService with a mocked client and model selector."""
    mock_client = MagicMock()
    mock_selector = MagicMock()
    mock_selector.current_model = "gemini-2.5-flash"
    mock_selector.advance_on_rate_limit.return_value = False
    svc = TranscriptionService(mock_client, mock_selector)
    return svc, mock_client, mock_selector


# ── __init__ ──────────────────────────────────────────────────────────


class TestInit:
    def test_stores_client_and_selector(self):
        mock_client = MagicMock()
        mock_selector = MagicMock()
        svc = TranscriptionService(mock_client, mock_selector)
        assert svc._client is mock_client
        assert svc._model_selector is mock_selector


# ── _transcribe_audio routing ─────────────────────────────────────────


class TestTranscribeAudioRouting:
    def test_inline_when_small(self, transcription_svc):
        svc, _, _ = transcription_svc
        with (
            patch("src.transcriber.os.path.getsize", return_value=10 * 1024 * 1024),
            patch.object(svc, "_transcribe_inline", return_value="text") as m_inline,
            patch.object(svc, "_transcribe_upload") as m_upload,
        ):
            result = svc._transcribe_audio("/audio.wav", "prompt", "gemini-2.5-flash")
        m_inline.assert_called_once()
        m_upload.assert_not_called()
        assert result == "text"

    def test_upload_when_large(self, transcription_svc):
        svc, _, _ = transcription_svc
        with (
            patch("src.transcriber.os.path.getsize", return_value=25 * 1024 * 1024),
            patch.object(svc, "_transcribe_inline") as m_inline,
            patch.object(svc, "_transcribe_upload", return_value="text") as m_upload,
        ):
            svc._transcribe_audio("/audio.wav", "prompt", "gemini-2.5-flash")
        m_upload.assert_called_once()
        m_inline.assert_not_called()


# ── _transcribe_inline ────────────────────────────────────────────────


class TestTranscribeInline:
    def test_reads_bytes_and_calls_generate(self, transcription_svc):
        svc, mock_client, _ = transcription_svc
        mock_response = MagicMock()
        mock_response.text = "transcription text"
        mock_client.models.generate_content.return_value = mock_response

        with patch("src.transcriber.Path.read_bytes", return_value=b"audio-data"):
            result = svc._transcribe_inline("/audio.wav", "prompt", "gemini-2.5-flash")

        assert result == "transcription text"
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-2.5-flash"


# ── _transcribe_upload ────────────────────────────────────────────────


class TestTranscribeUpload:
    def test_upload_poll_success(self, transcription_svc):
        svc, client, _ = transcription_svc
        uploaded = MagicMock()
        uploaded.name = "file-123"
        state_active = MagicMock()
        state_active.name = "ACTIVE"
        uploaded.state = state_active
        client.files.upload.return_value = uploaded
        mock_response = MagicMock()
        mock_response.text = "transcribed"
        client.models.generate_content.return_value = mock_response

        with patch("src.transcriber.time.sleep"):
            result = svc._transcribe_upload("/audio.wav", "prompt", "gemini-2.5-flash")

        assert result == "transcribed"
        client.files.delete.assert_called_once_with(name="file-123")
        call_kwargs = client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-2.5-flash"

    def test_poll_timeout(self, transcription_svc):
        svc, client, _ = transcription_svc
        uploaded = MagicMock()
        uploaded.name = "file-123"
        state_processing = MagicMock()
        state_processing.name = "PROCESSING"
        uploaded.state = state_processing
        client.files.upload.return_value = uploaded
        client.files.get.return_value = uploaded

        with (
            patch("src.transcriber.time.sleep"),
            pytest.raises(TranscriptionError, match="timed out"),
        ):
            svc._transcribe_upload("/audio.wav", "prompt", "gemini-2.5-flash")

    def test_failed_state(self, transcription_svc):
        svc, client, _ = transcription_svc
        uploaded = MagicMock()
        uploaded.name = "file-123"
        state_failed = MagicMock()
        state_failed.name = "FAILED"
        uploaded.state = state_failed
        client.files.upload.return_value = uploaded

        with (
            patch("src.transcriber.time.sleep"),
            pytest.raises(TranscriptionError, match="failed"),
        ):
            svc._transcribe_upload("/audio.wav", "prompt", "gemini-2.5-flash")

    def test_cleanup_in_finally(self, transcription_svc):
        svc, client, _ = transcription_svc
        uploaded = MagicMock()
        uploaded.name = "file-123"
        state = MagicMock()
        state.name = "FAILED"
        uploaded.state = state
        client.files.upload.return_value = uploaded

        with (
            patch("src.transcriber.time.sleep"),
            pytest.raises(TranscriptionError),
        ):
            svc._transcribe_upload("/audio.wav", "prompt", "gemini-2.5-flash")

        client.files.delete.assert_called_once_with(name="file-123")

    def test_cleanup_error_swallowed(self, transcription_svc):
        svc, client, _ = transcription_svc
        uploaded = MagicMock()
        uploaded.name = "file-123"
        state = MagicMock()
        state.name = "ACTIVE"
        uploaded.state = state
        client.files.upload.return_value = uploaded
        client.files.delete.side_effect = RuntimeError("delete failed")
        mock_response = MagicMock()
        mock_response.text = "ok"
        client.models.generate_content.return_value = mock_response

        with patch("src.transcriber.time.sleep"):
            result = svc._transcribe_upload("/audio.wav", "prompt", "gemini-2.5-flash")
        assert result == "ok"


# ── _summarize ────────────────────────────────────────────────────────


class TestSummarize:
    def test_sends_prompt_and_text(self, transcription_svc):
        svc, mock_client, _ = transcription_svc
        mock_response = MagicMock()
        mock_response.text = "summary"
        mock_client.models.generate_content.return_value = mock_response

        result = svc._summarize("transcript", "summarize: ", "gemini-2.5-flash")
        assert result == "summary"


# ── _generate_title ───────────────────────────────────────────────────


class TestGenerateTitle:
    def test_truncates_and_strips(self, transcription_svc):
        svc, mock_client, _ = transcription_svc
        mock_response = MagicMock()
        mock_response.text = "  My Title  "
        mock_client.models.generate_content.return_value = mock_response

        result = svc._generate_title("x" * 10000, "title: ", "gemini-2.5-flash")

        assert result == "My Title"
        call_args = mock_client.models.generate_content.call_args
        content = call_args.kwargs["contents"][0]
        # title_prompt + first 4000 chars
        assert len(content) == len("title: ") + 4000


# ── transcribe_and_summarize ─────────────────────────────────────────


class TestTranscribeAndSummarize:
    def test_full_pipeline(self, transcription_svc):
        svc, _, selector = transcription_svc
        with (
            patch.object(svc, "_transcribe_audio", return_value="transcript"),
            patch.object(svc, "_summarize", return_value="summary"),
            patch.object(svc, "_generate_title", return_value="title"),
        ):
            t, s, ti = svc.transcribe_and_summarize("/a.wav", "tp", "sp", "tip")
        assert (t, s, ti) == ("transcript", "summary", "title")
        selector.reset.assert_called_once()

    def test_transcription_error_passthrough(self, transcription_svc):
        svc, _, _ = transcription_svc
        with (
            patch.object(svc, "_transcribe_audio", side_effect=TranscriptionError("boom")),
            pytest.raises(TranscriptionError, match="boom"),
        ):
            svc.transcribe_and_summarize("/a.wav", "tp", "sp", "tip")

    def test_generic_exception_wrapping(self, transcription_svc):
        svc, _, _ = transcription_svc
        with (
            patch.object(svc, "_transcribe_audio", side_effect=RuntimeError("unexpected")),
            pytest.raises(TranscriptionError, match="unexpected"),
        ):
            svc.transcribe_and_summarize("/a.wav", "tp", "sp", "tip")

    def test_rate_limit_fallback(self, transcription_svc):
        """On 429, advance to the next model and retry."""
        svc, _, selector = transcription_svc

        from google.genai import errors as genai_errors

        rate_err = genai_errors.ClientError(429, {"error": {"message": "RESOURCE_EXHAUSTED"}})

        # First call raises 429, advance succeeds, second call works
        selector.advance_on_rate_limit.return_value = True
        call_count = 0

        def fake_transcribe(path, prompt, model):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise rate_err
            return "transcript"

        type(selector).current_model = PropertyMock(side_effect=["gemini-3-flash", "gemini-2.5-flash"])

        with (
            patch.object(svc, "_transcribe_audio", side_effect=fake_transcribe),
            patch.object(svc, "_summarize", return_value="summary"),
            patch.object(svc, "_generate_title", return_value="title"),
        ):
            t, s, ti = svc.transcribe_and_summarize("/a.wav", "tp", "sp", "tip")

        assert t == "transcript"
        selector.advance_on_rate_limit.assert_called_once()

    def test_rate_limit_all_exhausted(self, transcription_svc):
        """When all models are rate-limited, raise TranscriptionError."""
        svc, _, selector = transcription_svc

        from google.genai import errors as genai_errors

        rate_err = genai_errors.ClientError(429, {"error": {"message": "RESOURCE_EXHAUSTED"}})

        selector.advance_on_rate_limit.return_value = False

        with (
            patch.object(svc, "_transcribe_audio", side_effect=rate_err),
            pytest.raises(TranscriptionError, match="429"),
        ):
            svc.transcribe_and_summarize("/a.wav", "tp", "sp", "tip")
