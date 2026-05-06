import argparse
import csv
import sqlite3
from pathlib import Path
from zipfile import ZipFile

from youtube_history_pipeline import (
    command_export,
    command_parse,
    extract_video_id,
    find_watch_history_paths,
    parse_watch_history_html,
)


def test_extracts_video_id_from_watch_url():
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extracts_video_id_from_shorts_url():
    assert extract_video_id("https://www.youtube.com/shorts/abc123SHORT") == "abc123SHORT"


def test_extracts_video_id_from_youtu_be_url():
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ?t=42") == "dQw4w9WgXcQ"


def test_rejects_non_youtube_hosts():
    assert extract_video_id("https://notyoutube.com/watch?v=dQw4w9WgXcQ") is None


def test_finds_watch_history_path_inside_fake_zip(tmp_path):
    zip_path = tmp_path / "takeout.zip"
    expected_path = "Takeout/YouTube and YouTube Music/history/watch-history.html"

    with ZipFile(zip_path, "w") as zip_file:
        zip_file.writestr("Takeout/YouTube and YouTube Music/history/search-history.html", "")
        zip_file.writestr(expected_path, "<html></html>")

    with ZipFile(zip_path) as zip_file:
        assert find_watch_history_paths(zip_file) == [expected_path]


def test_parses_sample_html_into_expected_records():
    fixture = Path("tests/fixtures/watch-history.html").read_text(encoding="utf-8")

    records = parse_watch_history_html(fixture, source_takeout_file="sample.zip")

    assert len(records) == 2
    assert records[0].video_id == "dQw4w9WgXcQ"
    assert records[0].title_from_history == "Example Watch Video"
    assert records[0].channel_from_history == "Example Channel"
    assert records[0].watched_at == "May 5, 2026, 10:15:00 AM AEST"
    assert records[0].source_takeout_file == "sample.zip"
    assert records[1].video_id == "abc123SHORT"
    assert records[1].is_short_url is True


def test_parse_persists_rows_and_export_creates_csv(tmp_path):
    fixture_html = Path("tests/fixtures/watch-history.html").read_text(encoding="utf-8")
    takeout_dir = tmp_path / "takeout_zips"
    takeout_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    db_path = output_dir / "test.sqlite"
    csv_path = output_dir / "test.csv"
    zip_path = takeout_dir / "sample.zip"

    with ZipFile(zip_path, "w") as zip_file:
        zip_file.writestr("Takeout/YouTube and YouTube Music/history/watch-history.html", fixture_html)

    args = argparse.Namespace(
        takeout_dir=str(takeout_dir),
        database=str(db_path),
        csv=str(csv_path),
    )

    assert command_parse(args) == 0

    with sqlite3.connect(db_path) as conn:
        row_count = conn.execute("SELECT COUNT(*) FROM watch_history").fetchone()[0]
        assert row_count == 2

    assert command_export(args) == 0
    assert csv_path.exists()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["video_id"] == "dQw4w9WgXcQ"
