"""
YouTube Automation System — automated multi-channel YouTube uploader.
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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.config_loader import load_settings, load_channels
from modules.sheets_reader import SheetsReader
from modules.drive_downloader import DriveDownloader
from modules.youtube_uploader import YouTubeUploader
from modules.description_formatter import format_description

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

COUNTS_FILE = Path(__file__).parent / "data" / "upload_counts.json"


def _divider(char="─", width=60):
    print(char * width)


def _load_counts():
    if COUNTS_FILE.exists():
        with open(COUNTS_FILE) as f:
            return json.load(f)
    return {}


def _save_counts(counts):
    COUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COUNTS_FILE, "w") as f:
        json.dump(counts, f, indent=2)


def process_channel(tab_name, cfg, settings, sheets, downloader, max_per_run, counts, max_per_day):
    rl = settings["rate_limit"]
    delay_min = rl.get("delay_min_seconds", 90)
    delay_max = rl.get("delay_max_seconds", 120)

    today = time.strftime("%Y-%m-%d")
    day_key = f"{tab_name}::{today}"
    daily_count = counts.get(day_key, 0)

    if daily_count >= max_per_day:
        print(f"\n  [{tab_name}] Daily cap reached ({daily_count}/{max_per_day}) — skipping")
        return 0

    pending, skipped = sheets.get_pending_uploads(tab_name)

    _divider()
    print(f"  Channel : {tab_name}")
    print(f"  Pending : {len(pending)}  |  Skipped: {len(skipped)}  |  Daily uploads today: {daily_count}/{max_per_day}")
    _divider()

    for item in skipped:
        logger.info("[%s] Row %d skipped — %s", tab_name, item["row"], item["status"])

    uploader = YouTubeUploader(cfg["token_file"])
    uploaded = 0

    for item in pending[:max_per_run]:
        if daily_count >= max_per_day:
            logger.info("[%s] Daily cap hit mid-run — stopping", tab_name)
            break

        logger.info("[%s] Row %d: %s (scheduled %s)", tab_name, item["row_idx"], item["title"], item["scheduled_at"])

        tmp_path = None
        try:
            tmp_path = downloader.download(item["video_link"], item["title"])
            if not tmp_path:
                logger.error("[%s] Row %d: download failed — skipping", tab_name, item["row_idx"])
                sheets.update_completed(tab_name, item["row_idx"], settings["completed"]["failed"])
                continue

            description = format_description(item["description"])
            tags = [t.strip() for t in item["tags"].split(",") if t.strip()]

            video_id = uploader.upload_video(
                file_path=tmp_path,
                title=item["title"],
                description=description,
                tags=tags,
                publish_at=item["scheduled_at"],
                category_id=settings["upload"].get("category_id", "22"),
                made_for_kids=settings["upload"].get("made_for_kids", False),
            )

            if video_id:
                sheets.update_completed(tab_name, item["row_idx"], settings["completed"]["success"])
                sheets.update_video_id(tab_name, item["row_idx"], video_id)

                if item.get("playlist"):
                    playlist_id = uploader.get_or_create_playlist(item["playlist"])
                    if playlist_id:
                        uploader.add_to_playlist(video_id, playlist_id)

                uploaded += 1
                daily_count += 1
                counts[day_key] = daily_count
                _save_counts(counts)
            else:
                sheets.update_completed(tab_name, item["row_idx"], settings["completed"]["failed"])

        except Exception as exc:
            logger.exception("[%s] Row %d: unexpected error — %s", tab_name, item["row_idx"], exc)
            sheets.update_completed(tab_name, item["row_idx"], settings["completed"]["failed"])

        finally:
            if tmp_path and Path(tmp_path).exists():
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        if uploaded < max_per_run and item != pending[min(max_per_run, len(pending)) - 1]:
            delay = random.randint(delay_min, delay_max)
            logger.info("Waiting %ds before next upload...", delay)
            time.sleep(delay)

    return uploaded


def main():
    settings = load_settings()
    channels = load_channels(settings["channels_config"])
    master_token = settings["credentials"]["master_token"]

    rl = settings["rate_limit"]
    max_per_run = rl.get("max_uploads_per_run", 6)
    max_per_day = rl.get("max_uploads_per_day", 50)
    channel_delay = rl.get("delay_between_channels", 600)

    counts = _load_counts()

    sheets = SheetsReader(
        token_file=master_token,
        spreadsheet_id=settings["spreadsheet_id"],
        settings=settings,
    )
    downloader = DriveDownloader(master_token)

    all_tabs = sheets.get_sheet_tabs()
    active_channels = [t for t in all_tabs if t in channels]

    _divider("═")
    print(f"  YOUTUBE AUTOMATION SYSTEM")
    print(f"  Channels: {len(channels)}  |  Run budget: {max_per_run}/channel  |  Daily cap: {max_per_day}/channel")
    _divider("═")

    for idx, tab_name in enumerate(active_channels):
        done = process_channel(
            tab_name, channels[tab_name], settings,
            sheets, downloader, max_per_run, counts, max_per_day,
        )
        logger.info("[%s] Uploaded %d video(s) this run", tab_name, done)

        if idx < len(active_channels) - 1:
            logger.info("Waiting %ds before next channel...", channel_delay)
            time.sleep(channel_delay)

    _divider("═")
    print("  Run complete.")
    _divider("═")


if __name__ == "__main__":
    main()
