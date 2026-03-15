import re
from datetime import datetime

from notion_client import Client

from src.config import RECORDINGS_DIR

MAX_RICH_TEXT_LENGTH = 2000
MAX_BLOCKS_PER_REQUEST = 100


class NotionService:
    def __init__(self, token: str, database_id: str):
        self._client = Client(auth=token)
        self._database_id = database_id

    def save_transcription(
        self,
        title: str,
        date: datetime,
        duration_seconds: float,
        transcription: str,
        summary: str,
    ) -> str:
        """Create a page in the Meetings database. Returns the page URL."""
        duration_str = _format_duration(duration_seconds)

        summary_blocks = _text_to_blocks(summary)
        transcription_blocks = _text_to_blocks(transcription)

        # Build toggle heading 1 blocks with children
        # Notion API: children go inside the toggle heading block
        top_level_blocks = [
            _toggle_heading_1("AI Summary", summary_blocks),
            _toggle_heading_1("Transcription", transcription_blocks),
        ]

        # Flatten: each toggle heading counts as 1 block in the request,
        # but its children also count toward the 100-block limit per request.
        # We send the toggle headings with the page create, then append
        # overflow children separately if needed.
        page = self._client.pages.create(
            parent={"database_id": self._database_id},
            properties={
                "Name": {"title": [{"text": {"content": title}}]},
                "Date": {"date": {"start": date.isoformat()}},
                "Duration": {"rich_text": [{"text": {"content": duration_str}}]},
                "Status": {"select": {"name": "Transcribed"}},
            },
            children=top_level_blocks,
        )

        return page["url"]


def _toggle_heading_1(title: str, children: list[dict]) -> dict:
    """Create a toggle heading_1 block with nested children."""
    # Notion API limits children to 100 per block on creation.
    # For toggle headings, all children are nested inside.
    return {
        "object": "block",
        "type": "heading_1",
        "heading_1": {
            "rich_text": [{"type": "text", "text": {"content": title}}],
            "is_toggleable": True,
            "children": children[:MAX_BLOCKS_PER_REQUEST],
        },
    }


def save_transcription_locally(
    title: str, date: datetime, duration_seconds: float,
    transcription: str, summary: str,
) -> str:
    """Fallback: save transcription as a .txt file. Returns the file path."""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{title}.txt"
    filepath = RECORDINGS_DIR / filename
    duration_str = _format_duration(duration_seconds)
    content = (
        f"Title: {title}\nDate: {date.isoformat()}\nDuration: {duration_str}\n\n"
        f"## AI Summary\n{summary}\n\n"
        f"## Transcription\n{transcription}"
    )
    filepath.write_text(content)
    return str(filepath)


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _text_to_blocks(text: str) -> list[dict]:
    """Convert markdown text into Notion blocks (headings, bullets, bold, paragraphs)."""
    blocks = []
    lines = text.split("\n")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Headings
        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            block_type = f"heading_{min(level, 3)}"
            blocks.append({
                "object": "block",
                "type": block_type,
                block_type: {
                    "rich_text": _parse_inline_markdown(heading_text),
                },
            })
            continue

        # Bulleted list items: "- item" or "* item"
        bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet_match:
            item_text = bullet_match.group(1)
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": _parse_inline_markdown(item_text),
                },
            })
            continue

        # Divider: "---" or "***"
        if re.match(r"^[-*]{3,}$", stripped):
            blocks.append({
                "object": "block",
                "type": "divider",
                "divider": {},
            })
            continue

        # Regular paragraph
        rich_text = _parse_inline_markdown(stripped)
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": rich_text,
            },
        })

    return blocks


def _parse_inline_markdown(text: str) -> list[dict]:
    """Parse inline markdown (bold, italic) into Notion rich_text annotations."""
    parts = []
    # Split on **bold** and *italic* patterns
    pattern = re.compile(r"(\*\*(.+?)\*\*|\*(.+?)\*)")

    last_end = 0
    for match in pattern.finditer(text):
        # Add plain text before this match
        if match.start() > last_end:
            plain = text[last_end:match.start()]
            if plain:
                parts.extend(_chunk_rich_text(plain, {}))

        if match.group(2):
            # **bold**
            parts.extend(_chunk_rich_text(match.group(2), {"bold": True}))
        elif match.group(3):
            # *italic*
            parts.extend(_chunk_rich_text(match.group(3), {"italic": True}))

        last_end = match.end()

    # Add remaining plain text
    remaining = text[last_end:]
    if remaining:
        parts.extend(_chunk_rich_text(remaining, {}))

    return parts or [{"type": "text", "text": {"content": ""}}]


def _chunk_rich_text(content: str, annotations: dict) -> list[dict]:
    """Split text into chunks respecting MAX_RICH_TEXT_LENGTH, with annotations."""
    chunks = []
    while content:
        chunk = content[:MAX_RICH_TEXT_LENGTH]
        content = content[MAX_RICH_TEXT_LENGTH:]
        rt = {"type": "text", "text": {"content": chunk}}
        if annotations:
            rt["annotations"] = annotations
        chunks.append(rt)
    return chunks
