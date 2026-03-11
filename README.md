# Open Transcribe

macOS menu bar app that records meetings, transcribes via Gemini API, and saves to Notion. Recordings are kept in `~/Documents/Open Transcribe/`.

## Fresh Mac Setup (Step by Step)

### 1. Install uv (Python package manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your terminal after installing.

### 2. Get API Keys

- **Gemini API key** — get one at [aistudio.google.com](https://aistudio.google.com/)
- **Notion integration** — create at [notion.so/my-integrations](https://www.notion.so/my-integrations), copy the **Internal Integration Secret**

### 3. Set Up Notion Database

1. Create a new database called **"Meetings"** in Notion
2. Add these properties:

| Property | Type      |
|----------|-----------|
| Name     | Title     |
| Date     | Date      |
| Duration | Rich text |
| Status   | Select    |

3. Click **...** → **Connections** → add your integration
4. Copy the **database ID** from the URL: `https://notion.so/{workspace}/{database_id}?v=...`

### 4. Install BlackHole (Optional)

[BlackHole](https://existential.audio/blackhole/) is a virtual audio driver for capturing system audio. Without it, the app records from your default microphone instead.

### 5. Build the App

```bash
git clone <repo-url> && cd open-transcribe
uv sync --group dev
uv run python setup.py py2app
```

### 6. Install

1. Drag `dist/Open Transcribe.app` into `/Applications`
2. Double-click to launch — the icon appears in the menu bar (no dock icon)
3. Click the icon → **Settings** → enter your Gemini API key, Notion token, and database ID → save the file
4. Click **Reload Config**

### 7. Auto-Start on Login (Optional)

**System Settings → General → Login Items** → click **+** → select **Open Transcribe**

## Usage

1. Click the **Open Transcribe** icon in the menu bar
2. Click **Start Recording** — "REC" appears next to the icon
3. Speak / have your meeting
4. Click **Stop Recording** — "..." appears while processing
5. A notification appears when the transcription is saved to Notion

Recordings are always saved to `~/Documents/Open Transcribe/`.

## Development

To run from source without building the `.app`:

```bash
uv sync
uv run python main.py
```

## Settings

Click **Settings** in the menu bar dropdown to update API keys at any time. Config is stored at `~/.open-transcribe/config.json`.

## Fallback Behavior

- **No BlackHole?** Records from default microphone instead
- **Notion fails?** Saves transcription as `.txt` in `~/Documents/Open Transcribe/`
- **Missing API keys?** Click Settings, add your keys, then Reload Config
