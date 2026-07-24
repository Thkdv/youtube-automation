"""
Multi-channel YouTube uploader.
Reads metadata from Google Sheets, downloads from Drive, uploads to YouTube
with scheduling and playlist assignment. Runs once per hour via cron.

Rate limiting rules — applied PER CHANNEL,
since YouTube's upload rate limit itself is tracked per channel:
  - 90s minimum delay between every upload
  - Max 6 uploads per cron run, per channel
  - Max 50 uploads per day, per channel
  - 10 minute wait between switching channels
  - Channels processed one at a time, never in parallel

Note: policy/spam enforcement can still cascade across channels sharing
the same Google account, so the delay and stagger rules are kept regardless
of per-channel budgeting.
"""
import json
import logging
import os
import random
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.config_loader import load_settings, load_channels
from modules.description_formatter import format_description
from modules.drive_downloader import DriveDownloader
from modules.sheets_reader import SheetsReader
from modules.youtube_uploader import YouTubeUploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logging.getLogger("googleapiclient").setLevel(logging.ERROR)
logging.getLogger("google").setLevel(logging.ERROR)

logger = logging.getLogger("main")

COUNTER_FILE = Path(__file__).parent / "data" / "upload_counts.json"


def _divider(char="─", width=60):
    print(char * width)


# ── Daily upload counter (per channel) ──────────────────────────────────────────

def _load_counts():
    today = str(date.today())
    if COUNTER_FILE.exists():
        try:
            data = json.loads(COUNTER_FILE.read_text())
            if data.get("date") == today:
                return data
        except (json.JSONDecodeError, KeyError):
            pass
    return {"date": today, "channels": {}}


def _save_counts(counts):
    COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)
    COUNTER_FILE.write_text(json.dumps(counts))


def _channel_total(counts, tab_name):
    return counts["channels"].get(tab_name, 0)


def _channel_increment(counts, tab_name):
    counts["channels"][tab_name] = counts["channels"].get(tab_name, 0) + 1
    _save_counts(counts)


# ── Channel processor ──────────────────────────────────────────────────────────

def process_channel(tab_name, channel_cfg, settings, sheets, downloader, max_per_run, counts, max_per_day):
    token_file = channel_cfg.get("token_file", "")

    if not Path(token_file).exists():
        logger.error("[%s] OAuth token missing: %s", tab_name, token_file)
        return 0

    pending, skipped = sheets.get_pending_uploads(tab_name)

    _divider()
    print(f"  Channel : {tab_name}")
    for s in skipped:
        prefix = "├─" if s != skipped[-1] else "└─"
        print(f"  {prefix} Row {s['row']} — {s['status']}")

    daily_used = _channel_total(counts, tab_name)
    daily_remaining = max_per_day - daily_used
    effective_budget = min(max_per_run, daily_remaining)

    if not pending or effective_budget <= 0:
        if not pending:
            label = "No uploads due"
        elif daily_remaining <= 0:
            label = f"Daily cap reached for this channel ({max_per_day}/day)"
        else:
            label = "Run budget exhausted for this channel"
        print(f"  └─ {label}")
        _divider()
        return 0

    print(f"  └─ {len(pending)} queued  |  run budget: {effective_budget}  |  daily remaining: {daily_remaining}")
    _divider()

    try:
        uploader = YouTubeUploader(token_file)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("[%s] %s", tab_name, exc)
        return 0

    upload_cfg = settings.get("upload", {})
    rl = settings.get("rate_limit", {})
    delay_min = rl.get("delay_min_seconds", 90)
    delay_max = rl.get("delay_max_seconds", 120)
    uploads_done = 0

    for item in pending:
        if uploads_done >= effective_budget:
            print(f"  Budget reached — stopping at {uploads_done} upload(s) this channel.\n")
            break

        row = item["row_idx"]
        title = item["title"]

        print(f"\n  Row     : {row}")
        print(f"  Title   : {title}")
        print(f"  Schedule: {item['scheduled_at'].strftime('%d %b %Y %H:%M %Z')}")

        with tempfile.TemporaryDirectory() as tmpdir:
            background_path = os.path.join(tmpdir, "background.mp4")

            if not downloader.download(item["video_link"], background_path):
                sheets.update_completed(tab_name, row, settings["completed"]["failed"])
                print(f"  Result  : FAILED (download error)\n")
                continue

            video_path = background_path
            description = format_description(item["description"])
            tags = [t.strip() for t in item["tags"].split(",") if t.strip()]

            video_id = uploader.upload_video(
                file_path=video_path,
                title=title,
                description=description,
                tags=tags,
                publish_at=item["scheduled_at"],
                category_id=upload_cfg.get("category_id", "22"),
                made_for_kids=upload_cfg.get("made_for_kids", False),
            )

        if video_id:
            sheets.update_video_id(tab_name, row, video_id)
            print(f"  URL     : https://youtu.be/{video_id}")
            if item["playlist"]:
                playlist_id = uploader.get_or_create_playlist(item["playlist"])
                if playlist_id:
                    uploader.add_to_playlist(video_id, playlist_id)
                    print(f"  Playlist: {item['playlist']}")

        result = settings["completed"]["success"] if video_id else settings["completed"]["failed"]
        sheets.update_completed(tab_name, row, result)
        print(f"  Result  : {result}\n")

        if video_id:
            uploads_done += 1
            _channel_increment(counts, tab_name)

            remaining = effective_budget - uploads_done
            if remaining > 0 and item != pending[-1]:
                delay = random.randint(delay_min, delay_max)
                print(f"  Waiting {delay}s before next upload (anti-spam delay)...\n")
                time.sleep(delay)

    return uploads_done


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    settings = load_settings()
    channels = load_channels(settings["channels_config"])

    if not channels:
        print("No channels configured.")
        return

    master_token = settings["credentials"]["master_token"]
    sheets = SheetsReader(
        token_file=master_token,
        spreadsheet_id=settings["spreadsheet_id"],
        settings=settings,
    )
    downloader = DriveDownloader(master_token)

    all_tabs = sheets.get_sheet_tabs()
    rl = settings.get("rate_limit", {})
    max_per_run = rl.get("max_uploads_per_run", 6)
    max_per_day = rl.get("max_uploads_per_day", 50)
    channel_delay = rl.get("delay_between_channels", 600)

    counts = _load_counts()
    active_channels = [t for t in all_tabs if t in channels]

    _divider("═")
    print(f"  YOUTUBE AUTOMATION")
    print(f"  Channels: {len(channels)}  |  Run budget: {max_per_run}/channel  |  Daily cap: {max_per_day}/channel")
    _divider("═")

    for idx, tab_name in enumerate(active_channels):
        done = process_channel(
            tab_name, channels[tab_name], settings,
            sheets, downloader, max_per_run, counts, max_per_day,
        )

        if done > 0 and idx < len(active_channels) - 1:
            print(f"  Waiting {channel_delay}s before next channel...\n")
            time.sleep(channel_delay)

    _divider("═")
    summary = ", ".join(f"{t}: {_channel_total(counts, t)}/{max_per_day}" for t in active_channels)
    print(f"  Run complete. {summary}")
    _divider("═")


if __name__ == "__main__":
    main()
