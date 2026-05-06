Build a local Python CLI tool to process my Google Takeout YouTube watch history.

Goal:
I have multiple Google Takeout ZIP files. Each ZIP contains YouTube watch history at a path like:

takeout-20260505T234048Z-3-001.zip
  -> Takeout
    -> YouTube and YouTube Music
      -> history
        -> watch-history.html

I will place all ZIP files in ./takeout_zips/ when available. Meanwhile, start building the repo with fixtures/tests and a sample parser path.

The tool must read all ZIPs, extract watched YouTube videos and Shorts, enrich them with YouTube Data API metadata, store results in SQLite, and export a CSV I can open in Excel.

Important:
- Do not manually extract all ZIP files.
- Process one ZIP at a time.
- Extract only watch-history.html from each ZIP.
- Avoid loading all huge ZIP contents into memory.
- Use SQLite as the working store.
- Export final CSV with UTF-8 BOM so Excel opens it cleanly.
- Use YouTube Data API videos.list, not search.list.
- videos.list should batch up to 50 video IDs per request.
- Do not add AI classification yet.
- Do not add browser automation or scraping against Google My Activity.
- Do not add transcript scraping yet.

Runtime and tooling:
- Use Python 3.12+
- Use uv as the package manager
- Create pyproject.toml
- Create uv.lock if applicable
- Use argparse for CLI commands
- Use Makefile for common developer tasks
- Makefile must include a help target that explains each target and purpose

Project structure:
youtube-history-project/
  takeout_zips/
  output/
  temp/
  tests/
  youtube_history_pipeline.py
  pyproject.toml
  README.md
  Makefile
  .env.example
  .gitignore

Dependencies:
- requests
- python-dotenv
- pytest for tests
- sqlite3 from stdlib
- zipfile from stdlib
- pathlib from stdlib
- argparse from stdlib

Functional requirements:
1. Scan ./takeout_zips for .zip files.
2. For each ZIP, find files ending with watch-history.html inside the nested Takeout/YouTube and YouTube Music/history/ directory.
3. Be defensive with path matching because Takeout path names may vary slightly, but prioritize:
   - Takeout/YouTube and YouTube Music/history/watch-history.html
   - any path ending in history/watch-history.html
4. Parse each watch-history.html file.
5. Extract:
   - watched_at
   - title_from_history
   - channel_from_history if available
   - url
   - video_id
   - is_short_url
   - source_takeout_file
   - raw_text
6. Support normal video URLs:
   - https://www.youtube.com/watch?v=VIDEO_ID
7. Support Shorts URLs:
   - https://www.youtube.com/shorts/VIDEO_ID
8. Support youtu.be URLs if present:
   - https://youtu.be/VIDEO_ID
9. Deduplicate safely.
10. Store watch history rows in SQLite.
11. Store video metadata in a separate SQLite table.
12. Fetch metadata from YouTube Data API videos.list using:
   - part=snippet,contentDetails,statistics
   - max 50 IDs per request
13. Mark videos as unavailable/private/deleted if the API does not return metadata.
14. Export CSV with columns:
   - watched_at
   - title_from_history
   - channel_from_history
   - url
   - video_id
   - is_short_url
   - source_takeout_file
   - youtube_title
   - youtube_description
   - youtube_channel
   - youtube_published_at
   - youtube_duration
   - youtube_tags
   - youtube_category_id
   - youtube_view_count
   - youtube_like_count
   - metadata_status
   - metadata_updated_at

CLI commands:
- uv run python youtube_history_pipeline.py parse
- uv run python youtube_history_pipeline.py enrich
- uv run python youtube_history_pipeline.py export
- uv run python youtube_history_pipeline.py all
- uv run python youtube_history_pipeline.py summary

Configuration:
- Read YOUTUBE_API_KEY from .env
- Do not print the API key
- Do not commit .env
- Create .env.example with the same key names but blank values:
  YOUTUBE_API_KEY=

Required API keys:
- YOUTUBE_API_KEY is required only for metadata enrichment.
- Parsing and CSV export should work without YOUTUBE_API_KEY, but metadata fields will remain empty or not enriched.
- README must clearly explain how to create a YouTube Data API key and where to place it.

Makefile requirements:
Create common targets:
- help - show available targets and descriptions
- install - install dependencies using uv
- parse - parse Takeout ZIPs into SQLite
- enrich - fetch YouTube metadata
- export - export CSV
- all - run parse, enrich, export
- summary - print database summary
- test - run pytest
- clean - remove temp files
- clean-output - remove generated output files
- lint or format if you add formatting tools

README requirements:
Document everything from a developer point of view:
- What this repo does
- Why Google Takeout is used
- Expected ZIP structure
- Folder structure
- Requirements
- uv setup
- Environment variables
- Required API keys
- How to run each command
- How to use the Makefile
- What each output file means
- SQLite schema overview
- CSV column explanation
- How metadata enrichment works
- Why videos.list is used instead of search.list
- Known limitations:
  - unavailable/private/deleted videos may not return metadata
  - Takeout HTML format may vary
  - watched_at timezone parsing may be best effort
  - no AI classification yet
  - no transcript extraction yet
- Troubleshooting section for:
  - no ZIP files found
  - no watch-history.html found
  - missing YOUTUBE_API_KEY
  - quota/API errors
  - malformed ZIP files
  - Excel display issues

Safety:
- Do not print API key.
- Add .gitignore for:
  .env
  output/*.sqlite
  output/*.csv
  temp/*
  takeout_zips/*
  __pycache__/
  .pytest_cache/
  .venv/
- Preserve .gitkeep files if needed so empty folders exist in git.

Testing:
- Add a tiny sample watch-history HTML fixture under tests/fixtures/.
- Add tests for:
  - extracting video ID from watch URL
  - extracting video ID from shorts URL
  - extracting video ID from youtu.be URL
  - finding watch-history.html path inside a fake ZIP
  - parsing sample HTML into expected records
- Tests must not call the real YouTube API.

Implementation guidance:
- Keep implementation simple and maintainable.
- Prefer functions with single responsibility.
- Add logging or clear console output, but avoid noisy debug logs.
- Ensure repeated runs are safe and idempotent where practical.
- Do not over-engineer with classes unless useful.
- Build the boring reliable parser first.
