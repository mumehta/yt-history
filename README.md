# YouTube History Pipeline

Local Python CLI tool to process Google Takeout YouTube watch history ZIP files, enrich with YouTube Data API metadata, store results in SQLite, and export Excel-friendly CSV.

## What this repo does

1. Scans `takeout_zips/*.zip`
2. Finds `watch-history.html` inside each ZIP
3. Parses watched entries (videos + Shorts + `youtu.be`)
4. Stores parsed rows in SQLite
5. Enriches metadata via `videos.list` in batches of up to 50 IDs
6. Exports combined data to CSV with UTF-8 BOM

   <img width="2527" height="531" alt="history-csv" src="https://github.com/user-attachments/assets/66ee9473-3c2e-40c4-869e-6cd8196c943a" />

## Why Google Takeout

Google Takeout is an official export path and avoids browser automation/scraping of My Activity.

## Get the Google Takeout ZIP

1. Go to [Google Takeout](https://takeout.google.com/).
2. Click `Deselect all`.
3. Select `YouTube and YouTube Music`.
4. Keep `history` included in the YouTube export options.
5. Choose `.zip` as the export file type and create the export.
6. Download the generated Takeout ZIP when Google emails you that it is ready.
7. Place the ZIP file directly under `takeout_zips/`.

Do not unzip the archive. The parser reads `takeout_zips/*.zip` directly, so files such as `takeout-20260506T000000Z-001.zip` can stay as downloaded.

## Expected ZIP structure

Primary path:

```text
Takeout/YouTube and YouTube Music/history/watch-history.html
```

Fallback path matching:

```text
.../history/watch-history.html
```

## Folder structure

```text
yt-history/
  docs/
  output/
  takeout_zips/
  temp/
  tests/
    fixtures/
  youtube_history_pipeline.py
  pyproject.toml
  README.md
  Makefile
  .env.example
  .gitignore
```

## Requirements

- Python 3.12+
- `uv`

## Setup

```bash
uv sync
```

## Environment variables

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Set:

```text
YOUTUBE_API_KEY=
```

`YOUTUBE_API_KEY` is required for `enrich` only.

## How to get YouTube API key

1. Open Google Cloud Console
2. Create/select project
3. Enable **YouTube Data API v3**
4. Create credentials -> API key
5. Restrict key to **YouTube Data API v3**
6. Save in `.env`

## CLI commands

```bash
uv run python youtube_history_pipeline.py parse
uv run python youtube_history_pipeline.py enrich
uv run python youtube_history_pipeline.py export
uv run python youtube_history_pipeline.py all
uv run python youtube_history_pipeline.py summary
uv run python youtube_history_pipeline.py topic --topics ai,python --format lines
```

`all` runs `parse -> enrich -> export` and requires `YOUTUBE_API_KEY` for the enrich step.

## Makefile commands

```bash
make help
make install
make parse
make enrich
make export
make all
make summary
make topic TOPIC=ai,python TOPIC_FORMAT=lines
make topic TOPIC=ai,python TOPIC_FORMAT=row
make test
make clean
make clean-output
```

## Output files

- `output/youtube_history.sqlite`: parsed history and metadata tables
- `output/youtube_history.csv`: exported combined data (UTF-8 BOM)

## Open CSV in Excel

For large exports, import from inside Excel instead of double-clicking the CSV file.

1. Open Excel with a blank workbook.
2. Go to `Data` -> `From Text/CSV`.
3. Select `output/youtube_history.csv`.
4. In import options:
   - File origin: `65001: Unicode (UTF-8)`
   - Delimiter: `Comma`
   - Data type detection: `Do not detect data types`
5. Click `Load`.

If Excel still struggles with memory on very large files, import into LibreOffice Calc first and save as `.xlsx`.

## SQLite schema overview

`watch_history`:
- watched_at
- title_from_history
- channel_from_history
- url
- video_id
- is_short_url
- source_takeout_file
- raw_text

`video_metadata`:
- video_id
- youtube_title
- youtube_description
- youtube_channel
- youtube_published_at
- youtube_duration
- youtube_tags (JSON array string when present)
- youtube_category_id
- youtube_view_count
- youtube_like_count
- metadata_status
- metadata_updated_at

## CSV columns

```text
watched_at
title_from_history
channel_from_history
url
video_id
is_short_url
source_takeout_file
youtube_title
youtube_description
youtube_channel
youtube_published_at
youtube_duration
youtube_tags
youtube_category_id
youtube_view_count
youtube_like_count
metadata_status
metadata_updated_at
```

## How metadata enrichment works

- Uses `videos.list` with:
  - `part=snippet,contentDetails,statistics`
  - batches of up to 50 IDs
- Uses direct `video_id` lookup from parsed history
- Missing IDs from API response are marked:
  - `metadata_status=unavailable_or_private_or_deleted`

## Why `videos.list` and not `search.list`

History already contains exact video IDs. `videos.list` is deterministic and avoids ambiguous search ranking.

## Known limitations

- Unavailable/private/deleted videos may not return metadata
- Takeout HTML format variations can affect `watched_at` parsing
- `watched_at` timezone parsing is best effort from raw Takeout text
- No AI classification
- No transcript extraction

## Troubleshooting

No ZIP files found:
- Ensure `.zip` files are under `takeout_zips/`

No `watch-history.html` found:
- Ensure archive contains YouTube history and path ends with `history/watch-history.html`

Missing `YOUTUBE_API_KEY`:
- `parse` and `export` work without key
- `enrich` requires key in `.env`
- `all` will stop at `enrich` without key

Quota/API errors:
- Check API enabled and quota in Google Cloud Console
- Confirm key is restricted to YouTube Data API v3

Malformed ZIP files:
- Re-download affected Takeout ZIP

Excel display issues:
- Export uses UTF-8 BOM to improve Excel encoding detection
