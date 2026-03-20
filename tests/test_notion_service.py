from datetime import datetime
from unittest.mock import MagicMock, patch

from src.notion_service import (
    MAX_BLOCKS_PER_REQUEST,
    NotionService,
    _chunk_rich_text,
    _format_duration,
    _parse_inline_markdown,
    _text_to_blocks,
    _toggle_heading_1,
    save_transcription_locally,
)

# ── NotionService.__init__ ────────────────────────────────────────────


class TestNotionServiceInit:
    @patch("src.notion_service.Client")
    def test_stores_client_and_db_id(self, mock_client_cls):
        svc = NotionService("tok", "db-123")
        mock_client_cls.assert_called_once_with(auth="tok")
        assert svc._database_id == "db-123"


# ── save_transcription ────────────────────────────────────────────────


class TestSaveTranscription:
    @patch("src.notion_service.Client")
    def test_correct_properties(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.pages.create.return_value = {"url": "https://notion.so/page"}

        svc = NotionService("tok", "db-123")
        dt = datetime(2024, 1, 15, 10, 30)
        url = svc.save_transcription("Title", dt, 125.0, "transcript", "summary")

        assert url == "https://notion.so/page"
        call_kwargs = mock_client.pages.create.call_args.kwargs
        props = call_kwargs["properties"]
        assert props["Name"]["title"][0]["text"]["content"] == "Title"
        assert props["Date"]["date"]["start"] == dt.isoformat()
        assert props["Duration"]["rich_text"][0]["text"]["content"] == "2m 5s"
        assert props["Status"]["select"]["name"] == "Transcribed"

    @patch("src.notion_service.Client")
    def test_children_are_two_toggle_headings(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.pages.create.return_value = {"url": "https://notion.so/page"}

        svc = NotionService("tok", "db-123")
        svc.save_transcription("T", datetime(2024, 1, 1), 60, "trans", "sum")

        children = mock_client.pages.create.call_args.kwargs["children"]
        assert len(children) == 2
        assert children[0]["type"] == "heading_1"
        assert children[0]["heading_1"]["is_toggleable"] is True
        assert children[1]["type"] == "heading_1"


# ── _toggle_heading_1 ────────────────────────────────────────────────


class TestToggleHeading1:
    def test_correct_structure(self):
        block = _toggle_heading_1("My Title", [{"child": 1}])
        assert block["type"] == "heading_1"
        assert block["heading_1"]["rich_text"][0]["text"]["content"] == "My Title"
        assert block["heading_1"]["is_toggleable"] is True
        assert block["heading_1"]["children"] == [{"child": 1}]

    def test_children_truncated_at_100(self):
        children = [{"child": i} for i in range(150)]
        block = _toggle_heading_1("T", children)
        assert len(block["heading_1"]["children"]) == MAX_BLOCKS_PER_REQUEST


# ── save_transcription_locally ────────────────────────────────────────


class TestSaveTranscriptionLocally:
    def test_creates_dir_and_writes(self, tmp_path):
        rec_dir = tmp_path / "recordings"
        with patch("src.notion_service.RECORDINGS_DIR", rec_dir):
            path = save_transcription_locally("Test Title", datetime(2024, 1, 15, 10, 0), 90, "trans", "sum")
        assert rec_dir.exists()
        content = (rec_dir / "Test Title.txt").read_text()
        assert "Title: Test Title" in content
        assert "Duration: 1m 30s" in content
        assert "## AI Summary" in content
        assert "## Transcription" in content
        assert path == str(rec_dir / "Test Title.txt")


# ── _format_duration ──────────────────────────────────────────────────


class TestFormatDuration:
    def test_zero(self):
        assert _format_duration(0) == "0s"

    def test_seconds_only(self):
        assert _format_duration(30) == "30s"

    def test_exactly_one_minute(self):
        assert _format_duration(60) == "1m 0s"

    def test_minutes_and_seconds(self):
        assert _format_duration(125) == "2m 5s"

    def test_float_truncation(self):
        assert _format_duration(125.9) == "2m 5s"


# ── _text_to_blocks ───────────────────────────────────────────────────


class TestTextToBlocks:
    def test_empty_string(self):
        assert _text_to_blocks("") == []

    def test_blank_lines_skipped(self):
        blocks = _text_to_blocks("\n\n\n")
        assert blocks == []

    def test_heading_h1(self):
        blocks = _text_to_blocks("# Title")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "heading_1"

    def test_heading_h2(self):
        blocks = _text_to_blocks("## Subtitle")
        assert blocks[0]["type"] == "heading_2"

    def test_heading_h3(self):
        blocks = _text_to_blocks("### Section")
        assert blocks[0]["type"] == "heading_3"

    def test_four_hashes_is_paragraph(self):
        blocks = _text_to_blocks("#### Not a heading")
        assert blocks[0]["type"] == "paragraph"

    def test_bullet_dash(self):
        blocks = _text_to_blocks("- item one")
        assert blocks[0]["type"] == "bulleted_list_item"

    def test_bullet_asterisk(self):
        blocks = _text_to_blocks("* item two")
        assert blocks[0]["type"] == "bulleted_list_item"

    def test_divider_dashes(self):
        blocks = _text_to_blocks("---")
        assert blocks[0]["type"] == "divider"

    def test_divider_asterisks(self):
        blocks = _text_to_blocks("***")
        assert blocks[0]["type"] == "divider"

    def test_paragraph(self):
        blocks = _text_to_blocks("Some text here")
        assert blocks[0]["type"] == "paragraph"

    def test_mixed_content(self):
        text = "# Title\n\nSome paragraph.\n\n- bullet\n\n---\n"
        blocks = _text_to_blocks(text)
        types = [b["type"] for b in blocks]
        assert types == ["heading_1", "paragraph", "bulleted_list_item", "divider"]

    def test_heading_with_inline_markdown(self):
        blocks = _text_to_blocks("## **Bold** heading")
        rt = blocks[0]["heading_2"]["rich_text"]
        # Should contain bold annotation
        bold_parts = [p for p in rt if p.get("annotations", {}).get("bold")]
        assert len(bold_parts) == 1


# ── _parse_inline_markdown ────────────────────────────────────────────


class TestParseInlineMarkdown:
    def test_plain_text(self):
        parts = _parse_inline_markdown("hello world")
        assert len(parts) == 1
        assert parts[0]["text"]["content"] == "hello world"

    def test_empty_string(self):
        parts = _parse_inline_markdown("")
        assert len(parts) == 1
        assert parts[0]["text"]["content"] == ""

    def test_bold(self):
        parts = _parse_inline_markdown("**bold**")
        assert len(parts) == 1
        assert parts[0]["text"]["content"] == "bold"
        assert parts[0]["annotations"]["bold"] is True

    def test_italic(self):
        parts = _parse_inline_markdown("*italic*")
        assert len(parts) == 1
        assert parts[0]["text"]["content"] == "italic"
        assert parts[0]["annotations"]["italic"] is True

    def test_mixed(self):
        parts = _parse_inline_markdown("hello **bold** and *italic* end")
        contents = [p["text"]["content"] for p in parts]
        assert "hello " in contents
        assert "bold" in contents
        assert " and " in contents
        assert "italic" in contents
        assert " end" in contents

    def test_bold_within_text(self):
        parts = _parse_inline_markdown("start **mid** end")
        assert len(parts) == 3
        assert parts[1]["annotations"]["bold"] is True


# ── _chunk_rich_text ──────────────────────────────────────────────────


class TestChunkRichText:
    def test_short_text(self):
        result = _chunk_rich_text("hello", {})
        assert len(result) == 1
        assert result[0]["text"]["content"] == "hello"
        assert "annotations" not in result[0]

    def test_exactly_2000(self):
        text = "a" * 2000
        result = _chunk_rich_text(text, {})
        assert len(result) == 1
        assert result[0]["text"]["content"] == text

    def test_over_2000(self):
        text = "a" * 4500
        result = _chunk_rich_text(text, {})
        assert len(result) == 3
        assert len(result[0]["text"]["content"]) == 2000
        assert len(result[1]["text"]["content"]) == 2000
        assert len(result[2]["text"]["content"]) == 500

    def test_with_annotations(self):
        result = _chunk_rich_text("hello", {"bold": True})
        assert result[0]["annotations"] == {"bold": True}

    def test_without_annotations(self):
        result = _chunk_rich_text("hello", {})
        assert "annotations" not in result[0]

    def test_empty_string(self):
        result = _chunk_rich_text("", {})
        assert result == []
