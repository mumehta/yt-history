from __future__ import annotations

import argparse
import codecs
import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import BinaryIO, Iterable, Sequence
from urllib.parse import parse_qs, urlparse
from zipfile import BadZipFile, ZipFile

import requests
from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_TAKEOUT_DIR = PROJECT_ROOT / "takeout_zips"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
DEFAULT_DATABASE_PATH = DEFAULT_OUTPUT_DIR / "youtube_history.sqlite"
DEFAULT_CSV_PATH = DEFAULT_OUTPUT_DIR / "youtube_history.csv"
YOUTUBE_VIDEOS_LIST_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_BATCH_SIZE = 50
UNAVAILABLE_STATUS = "unavailable_or_private_or_deleted"
OK_STATUS = "ok"


WATCH_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watched_at TEXT,
    title_from_history TEXT NOT NULL,
    channel_from_history TEXT,
    url TEXT NOT NULL,
    video_id TEXT NOT NULL,
    is_short_url INTEGER NOT NULL,
    source_takeout_file TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, watched_at, source_takeout_file, url)
);
"""

VIDEO_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS video_metadata (
    video_id TEXT PRIMARY KEY,
    youtube_title TEXT,
    youtube_description TEXT,
    youtube_channel TEXT,
    youtube_published_at TEXT,
    youtube_duration TEXT,
    youtube_tags TEXT,
    youtube_category_id TEXT,
    youtube_view_count INTEGER,
    youtube_like_count INTEGER,
    metadata_status TEXT NOT NULL,
    metadata_updated_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class WatchRecord:
    watched_at: str | None
    title_from_history: str
    channel_from_history: str | None
    url: str
    video_id: str
    is_short_url: bool
    source_takeout_file: str
    raw_text: str


@dataclass(frozen=True)
class VideoMetadataRecord:
    video_id: str
    youtube_title: str | None
    youtube_description: str | None
    youtube_channel: str | None
    youtube_published_at: str | None
    youtube_duration: str | None
    youtube_tags: str | None
    youtube_category_id: str | None
    youtube_view_count: int | None
    youtube_like_count: int | None
    metadata_status: str
    metadata_updated_at: str


class WatchHistoryHTMLParser(HTMLParser):
    """Streaming parser for Takeout watch-history.html content-cell entries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, list[tuple[str, str]]]] = []

        self._in_content_cell = False
        self._content_div_depth = 0
        self._text_parts: list[str] = []
        self._links: list[tuple[str, str]] = []

        self._current_href: str | None = None
        self._current_link_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}

        if not self._in_content_cell and tag == "div":
            class_value = attr_map.get("class", "")
            classes = set(class_value.split())
            if "content-cell" in classes:
                self._in_content_cell = True
                self._content_div_depth = 1
                self._text_parts = []
                self._links = []
                self._current_href = None
                self._current_link_text_parts = []
                return

        if not self._in_content_cell:
            return

        if tag == "div":
            self._content_div_depth += 1
            return

        if tag == "a":
            self._current_href = attr_map.get("href") or ""
            self._current_link_text_parts = []

    def handle_endtag(self, tag: str) -> None:
        if not self._in_content_cell:
            return

        if tag == "a" and self._current_href is not None:
            text = _normalize_ws("".join(self._current_link_text_parts))
            self._links.append((self._current_href, text))
            self._current_href = None
            self._current_link_text_parts = []
            return

        if tag == "div":
            self._content_div_depth -= 1
            if self._content_div_depth == 0:
                raw_text = _normalize_ws("".join(self._text_parts))
                self.rows.append((raw_text, self._links))
                self._in_content_cell = False
                self._content_div_depth = 0
                self._text_parts = []
                self._links = []

    def handle_data(self, data: str) -> None:
        if not self._in_content_cell:
            return
        self._text_parts.append(data)
        if self._current_href is not None:
            self._current_link_text_parts.append(data)


def extract_video_id(url: str) -> str | None:
    """Extract a YouTube video ID from supported watch, Shorts, and youtu.be URLs."""
    parsed = urlparse(url)
    host = _normalize_host(parsed.netloc)
    path_parts = [part for part in parsed.path.split("/") if part]

    if _is_youtube_host(host) or _is_youtube_nocookie_host(host):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        if len(path_parts) >= 2 and path_parts[0] == "shorts":
            return path_parts[1]

    if host == "youtu.be" and path_parts:
        return path_parts[0]

    return None


def is_short_url(url: str) -> bool:
    parsed = urlparse(url)
    host = _normalize_host(parsed.netloc)
    path_parts = [part for part in parsed.path.split("/") if part]
    return _is_youtube_host(host) and len(path_parts) >= 2 and path_parts[0] == "shorts"


def find_watch_history_paths(zip_file: ZipFile) -> list[str]:
    """Find likely watch-history.html members without extracting the whole archive."""
    candidates: list[tuple[int, str]] = []

    for name in zip_file.namelist():
        normalized = name.replace("\\", "/")
        lowered = normalized.lower()

        if lowered.endswith("takeout/youtube and youtube music/history/watch-history.html"):
            candidates.append((0, name))
        elif lowered.endswith("/history/watch-history.html") or lowered == "history/watch-history.html":
            candidates.append((1, name))

    return [name for _, name in sorted(candidates, key=lambda item: (item[0], item[1]))]


def parse_watch_history_html(html: str | bytes, source_takeout_file: str = "") -> list[WatchRecord]:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")

    parser = WatchHistoryHTMLParser()
    parser.feed(html)
    parser.close()
    return _rows_to_watch_records(parser.rows, source_takeout_file)


def parse_watch_history_stream(html_stream: BinaryIO, source_takeout_file: str = "") -> list[WatchRecord]:
    parser = WatchHistoryHTMLParser()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    while True:
        chunk = html_stream.read(1024 * 1024)
        if not chunk:
            break
        parser.feed(decoder.decode(chunk))

    parser.feed(decoder.decode(b"", final=True))
    parser.close()
    return _rows_to_watch_records(parser.rows, source_takeout_file)


def load_config() -> dict[str, str | None]:
    config: dict[str, str | None] = {}
    env_path = PROJECT_ROOT / ".env"

    if env_path.exists():
        config.update(dotenv_values(env_path))

    return config


def command_parse(args: argparse.Namespace) -> int:
    takeout_dir = Path(args.takeout_dir)
    database_path = Path(args.database)
    zip_paths = sorted(takeout_dir.glob("*.zip"))

    if not zip_paths:
        print(f"No ZIP files found in {takeout_dir}")
        return 0

    with open_database(database_path) as conn:
        init_db(conn)

        print(f"Found {len(zip_paths)} zip file(s) in {takeout_dir}")
        total_parsed = 0
        total_inserted = 0

        for index, zip_path in enumerate(zip_paths, start=1):
            print(f"[{index}/{len(zip_paths)}] Scanning {zip_path.name}")
            try:
                with ZipFile(zip_path) as zip_file:
                    history_paths = find_watch_history_paths(zip_file)
                    if not history_paths:
                        print(f"No watch-history.html found in {zip_path.name}")
                        continue

                    for history_path in history_paths:
                        info = zip_file.getinfo(history_path)
                        size_mb = info.file_size / (1024 * 1024)
                        print(
                            f"Parsing {zip_path.name}:{history_path} "
                            f"({size_mb:.1f} MB uncompressed)"
                        )
                        with zip_file.open(history_path) as html_file:
                            records = parse_watch_history_stream(
                                html_file, source_takeout_file=zip_path.name
                            )

                        inserted = insert_watch_records(conn, records)
                        total_parsed += len(records)
                        total_inserted += inserted
                        print(
                            f"Parsed {len(records)} record(s); inserted {inserted} new row(s) "
                            f"from {zip_path.name}:{history_path}"
                        )
            except BadZipFile:
                print(f"Malformed ZIP file skipped: {zip_path.name}")

        print(
            f"Parse complete. Parsed {total_parsed} record(s), inserted {total_inserted} "
            f"new row(s) into {database_path}"
        )
    return 0


def command_enrich(args: argparse.Namespace) -> int:
    database_path = Path(args.database)
    config = load_config()
    api_key = config.get("YOUTUBE_API_KEY")
    if not api_key:
        print("Missing YOUTUBE_API_KEY in .env. Enrichment skipped.")
        return 1

    with open_database(database_path) as conn:
        init_db(conn)
        video_ids = fetch_all_video_ids(conn)
        if not video_ids:
            print("No video IDs found in watch_history. Run parse first.")
            return 0

        print(f"Enriching metadata for {len(video_ids)} unique video ID(s)")
        now = utc_now_iso()
        total_ok = 0
        total_unavailable = 0

        for index, batch in enumerate(chunked(video_ids, YOUTUBE_BATCH_SIZE), start=1):
            print(f"Batch {index}: requesting metadata for {len(batch)} video ID(s)")
            try:
                found_map = fetch_videos_list_batch(batch, api_key)
            except requests.RequestException as exc:
                print(f"YouTube API request failed: {exc}")
                return 1
            except ValueError as exc:
                print(f"YouTube API response parsing failed: {exc}")
                return 1

            metadata_rows: list[VideoMetadataRecord] = []
            for video_id in batch:
                item = found_map.get(video_id)
                if item is None:
                    total_unavailable += 1
                    metadata_rows.append(
                        VideoMetadataRecord(
                            video_id=video_id,
                            youtube_title=None,
                            youtube_description=None,
                            youtube_channel=None,
                            youtube_published_at=None,
                            youtube_duration=None,
                            youtube_tags=None,
                            youtube_category_id=None,
                            youtube_view_count=None,
                            youtube_like_count=None,
                            metadata_status=UNAVAILABLE_STATUS,
                            metadata_updated_at=now,
                        )
                    )
                else:
                    total_ok += 1
                    metadata_rows.append(video_item_to_record(item, now=now))

            upsert_video_metadata_records(conn, metadata_rows)
            print(
                f"Batch {index} complete. ok={sum(1 for r in metadata_rows if r.metadata_status == OK_STATUS)} "
                f"unavailable={sum(1 for r in metadata_rows if r.metadata_status != OK_STATUS)}"
            )

        print(
            f"Enrichment complete. metadata ok={total_ok}, unavailable/private/deleted={total_unavailable}"
        )
    return 0


def command_export(args: argparse.Namespace) -> int:
    database_path = Path(args.database)
    csv_path = Path(args.csv)

    with open_database(database_path) as conn:
        init_db(conn)
        rows = fetch_export_rows(conn)
        if not rows:
            print("No parsed rows found in database. Run parse first.")
            return 0

        csv_path.parent.mkdir(parents=True, exist_ok=True)
        columns = [
            "watched_at",
            "title_from_history",
            "channel_from_history",
            "url",
            "video_id",
            "is_short_url",
            "source_takeout_file",
            "youtube_title",
            "youtube_description",
            "youtube_channel",
            "youtube_published_at",
            "youtube_duration",
            "youtube_tags",
            "youtube_category_id",
            "youtube_view_count",
            "youtube_like_count",
            "metadata_status",
            "metadata_updated_at",
        ]

        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

        print(f"Exported {len(rows)} row(s) to {csv_path}")
    return 0


def command_summary(args: argparse.Namespace) -> int:
    database_path = Path(args.database)
    with open_database(database_path) as conn:
        init_db(conn)
        counts = fetch_summary_counts(conn)

    print(f"Database: {database_path}")
    print(f"watch_history rows: {counts['watch_history_rows']}")
    print(f"watch_history unique video_ids: {counts['unique_video_ids']}")
    print(f"source Takeout files: {counts['source_takeout_files']}")
    print(f"video_metadata rows: {counts['video_metadata_rows']}")
    print(f"metadata ok: {counts['metadata_ok_rows']}")
    print(f"metadata unavailable/private/deleted: {counts['metadata_unavailable_rows']}")
    return 0


def command_all(args: argparse.Namespace) -> int:
    parse_result = command_parse(args)
    if parse_result != 0:
        return parse_result
    enrich_result = command_enrich(args)
    if enrich_result != 0:
        return enrich_result
    return command_export(args)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Process Google Takeout YouTube watch history.",
    )
    parser.add_argument(
        "--takeout-dir",
        default=str(DEFAULT_TAKEOUT_DIR),
        help="Directory containing Google Takeout ZIP files.",
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="SQLite database path.",
    )
    parser.add_argument(
        "--csv",
        default=str(DEFAULT_CSV_PATH),
        help="CSV output path.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_parser = subparsers.add_parser("parse", help="Parse Takeout ZIP files.")
    parse_parser.set_defaults(func=command_parse)

    enrich_parser = subparsers.add_parser("enrich", help="Fetch YouTube metadata.")
    enrich_parser.set_defaults(func=command_enrich)

    export_parser = subparsers.add_parser("export", help="Export CSV.")
    export_parser.set_defaults(func=command_export)

    all_parser = subparsers.add_parser("all", help="Run parse, enrich, and export.")
    all_parser.set_defaults(func=command_all)

    summary_parser = subparsers.add_parser("summary", help="Print database summary.")
    summary_parser.set_defaults(func=command_summary)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def open_database(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(WATCH_HISTORY_SCHEMA)
    conn.execute(VIDEO_METADATA_SCHEMA)
    conn.commit()


def insert_watch_records(conn: sqlite3.Connection, records: Sequence[WatchRecord]) -> int:
    if not records:
        return 0

    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO watch_history (
            watched_at,
            title_from_history,
            channel_from_history,
            url,
            video_id,
            is_short_url,
            source_takeout_file,
            raw_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                record.watched_at,
                record.title_from_history,
                record.channel_from_history,
                record.url,
                record.video_id,
                int(record.is_short_url),
                record.source_takeout_file,
                record.raw_text,
            )
            for record in records
        ],
    )
    conn.commit()
    return conn.total_changes - before


def fetch_all_video_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT video_id
        FROM watch_history
        WHERE video_id IS NOT NULL AND video_id != ''
        ORDER BY video_id
        """
    ).fetchall()
    return [row["video_id"] for row in rows]


def fetch_videos_list_batch(video_ids: Sequence[str], api_key: str) -> dict[str, dict]:
    params = {
        "part": "snippet,contentDetails,statistics",
        "id": ",".join(video_ids),
        "maxResults": str(min(len(video_ids), YOUTUBE_BATCH_SIZE)),
        "key": api_key,
    }
    response = requests.get(YOUTUBE_VIDEOS_LIST_URL, params=params, timeout=30)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        message = response.text.strip()
        raise requests.HTTPError(f"{exc} | response={message}") from exc

    data = response.json()
    items = data.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Unexpected videos.list response: items is not a list")

    by_id: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        video_id = item.get("id")
        if isinstance(video_id, str) and video_id:
            by_id[video_id] = item
    return by_id


def video_item_to_record(item: dict, now: str) -> VideoMetadataRecord:
    snippet = item.get("snippet", {}) if isinstance(item.get("snippet"), dict) else {}
    content_details = (
        item.get("contentDetails", {}) if isinstance(item.get("contentDetails"), dict) else {}
    )
    statistics = item.get("statistics", {}) if isinstance(item.get("statistics"), dict) else {}

    tags = snippet.get("tags")
    tags_json = json.dumps(tags, ensure_ascii=True) if isinstance(tags, list) else None
    video_id = str(item.get("id", ""))

    return VideoMetadataRecord(
        video_id=video_id,
        youtube_title=_to_text_or_none(snippet.get("title")),
        youtube_description=_to_text_or_none(snippet.get("description")),
        youtube_channel=_to_text_or_none(snippet.get("channelTitle")),
        youtube_published_at=_to_text_or_none(snippet.get("publishedAt")),
        youtube_duration=_to_text_or_none(content_details.get("duration")),
        youtube_tags=tags_json,
        youtube_category_id=_to_text_or_none(snippet.get("categoryId")),
        youtube_view_count=_to_int_or_none(statistics.get("viewCount")),
        youtube_like_count=_to_int_or_none(statistics.get("likeCount")),
        metadata_status=OK_STATUS,
        metadata_updated_at=now,
    )


def upsert_video_metadata_records(
    conn: sqlite3.Connection, metadata_rows: Sequence[VideoMetadataRecord]
) -> None:
    if not metadata_rows:
        return

    conn.executemany(
        """
        INSERT INTO video_metadata (
            video_id,
            youtube_title,
            youtube_description,
            youtube_channel,
            youtube_published_at,
            youtube_duration,
            youtube_tags,
            youtube_category_id,
            youtube_view_count,
            youtube_like_count,
            metadata_status,
            metadata_updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            youtube_title = excluded.youtube_title,
            youtube_description = excluded.youtube_description,
            youtube_channel = excluded.youtube_channel,
            youtube_published_at = excluded.youtube_published_at,
            youtube_duration = excluded.youtube_duration,
            youtube_tags = excluded.youtube_tags,
            youtube_category_id = excluded.youtube_category_id,
            youtube_view_count = excluded.youtube_view_count,
            youtube_like_count = excluded.youtube_like_count,
            metadata_status = excluded.metadata_status,
            metadata_updated_at = excluded.metadata_updated_at
        """,
        [
            (
                row.video_id,
                row.youtube_title,
                row.youtube_description,
                row.youtube_channel,
                row.youtube_published_at,
                row.youtube_duration,
                row.youtube_tags,
                row.youtube_category_id,
                row.youtube_view_count,
                row.youtube_like_count,
                row.metadata_status,
                row.metadata_updated_at,
            )
            for row in metadata_rows
        ],
    )
    conn.commit()


def fetch_export_rows(conn: sqlite3.Connection) -> list[dict[str, object]]:
    cursor = conn.execute(
        """
        SELECT
            w.watched_at,
            w.title_from_history,
            w.channel_from_history,
            w.url,
            w.video_id,
            w.is_short_url,
            w.source_takeout_file,
            m.youtube_title,
            m.youtube_description,
            m.youtube_channel,
            m.youtube_published_at,
            m.youtube_duration,
            m.youtube_tags,
            m.youtube_category_id,
            m.youtube_view_count,
            m.youtube_like_count,
            m.metadata_status,
            m.metadata_updated_at
        FROM watch_history w
        LEFT JOIN video_metadata m ON m.video_id = w.video_id
        ORDER BY w.id
        """
    )

    rows: list[dict[str, object]] = []
    for row in cursor.fetchall():
        row_dict = dict(row)
        row_dict["is_short_url"] = int(row_dict["is_short_url"]) if row_dict["is_short_url"] is not None else 0
        rows.append(row_dict)
    return rows


def fetch_summary_counts(conn: sqlite3.Connection) -> dict[str, int]:
    watch_history_rows = conn.execute("SELECT COUNT(*) FROM watch_history").fetchone()[0]
    unique_video_ids = conn.execute("SELECT COUNT(DISTINCT video_id) FROM watch_history").fetchone()[0]
    source_takeout_files = conn.execute(
        "SELECT COUNT(DISTINCT source_takeout_file) FROM watch_history"
    ).fetchone()[0]
    video_metadata_rows = conn.execute("SELECT COUNT(*) FROM video_metadata").fetchone()[0]
    metadata_ok_rows = conn.execute(
        "SELECT COUNT(*) FROM video_metadata WHERE metadata_status = ?", (OK_STATUS,)
    ).fetchone()[0]
    metadata_unavailable_rows = conn.execute(
        "SELECT COUNT(*) FROM video_metadata WHERE metadata_status != ?", (OK_STATUS,)
    ).fetchone()[0]

    return {
        "watch_history_rows": int(watch_history_rows),
        "unique_video_ids": int(unique_video_ids),
        "source_takeout_files": int(source_takeout_files),
        "video_metadata_rows": int(video_metadata_rows),
        "metadata_ok_rows": int(metadata_ok_rows),
        "metadata_unavailable_rows": int(metadata_unavailable_rows),
    }


def chunked(items: Sequence[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield list(items[index : index + size])


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _rows_to_watch_records(
    rows: list[tuple[str, list[tuple[str, str]]]], source_takeout_file: str
) -> list[WatchRecord]:
    records: list[WatchRecord] = []
    seen: set[tuple[str, str | None, str]] = set()

    for raw_text, links in rows:
        video_link = _pick_video_link(links)
        if video_link is None:
            continue

        url, title, video_id = video_link
        channel = _extract_channel_from_links(links, video_url=url)
        watched_at = _extract_watched_at(raw_text, title, channel)
        record_key = (video_id, watched_at, source_takeout_file)

        if record_key in seen:
            continue

        seen.add(record_key)
        records.append(
            WatchRecord(
                watched_at=watched_at,
                title_from_history=title,
                channel_from_history=channel,
                url=url,
                video_id=video_id,
                is_short_url=is_short_url(url),
                source_takeout_file=source_takeout_file,
                raw_text=raw_text,
            )
        )
    return records


def _extract_channel_from_links(links: list[tuple[str, str]], video_url: str) -> str | None:
    for href, text in links:
        if href == video_url:
            continue
        if text:
            return text
    return None


def _extract_watched_at(raw_text: str, title: str, channel: str | None) -> str | None:
    watched_at = raw_text
    for token in ("Watched", title, channel):
        if token:
            watched_at = watched_at.replace(token, " ", 1)

    watched_at = " ".join(watched_at.split())
    return watched_at or None


def _pick_video_link(links: list[tuple[str, str]]) -> tuple[str, str, str] | None:
    for href, text in links:
        video_id = extract_video_id(href)
        if video_id is None:
            continue
        return href, text, video_id
    return None


def _normalize_ws(value: str) -> str:
    return " ".join(unescape(value).split())


def _normalize_host(host: str) -> str:
    return host.split(":", 1)[0].lower()


def _is_youtube_host(host: str) -> bool:
    return host == "youtube.com" or host.endswith(".youtube.com")


def _is_youtube_nocookie_host(host: str) -> bool:
    return host == "youtube-nocookie.com" or host.endswith(".youtube-nocookie.com")


def _to_text_or_none(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if value is None:
        return None
    return str(value)


def _to_int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            return None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
