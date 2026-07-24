"""
Content Queue processor.
Reads pending rows from the Content Queue tab, runs the Phase 2A pipeline,
uploads the assembled video to Drive, then deposits a row in the target
channel tab for main.py to pick up and upload to YouTube.
"""
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from googleapiclient.discovery import build

from modules.auth import load_oauth_creds
from modules.config_loader import load_settings, load_channels
from modules.drive_downloader import DriveDownloader
from modules.drive_uploader import DriveUploader
from modules.pipeline import run_phase2a
from modules.sheets_reader import SheetsReader
from modules.video_assembler import append_clips

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("content_queue")

QUEUE_TAB = "Content Queue"
GENERATED_FOLDER_ID = "your-google-drive-folder-id"
APPROVED_MUSIC_PREFIX = "SUNO MUSIC_"  # a pasted music link is only used if its filename starts with this
MAX_ROWS_PER_RUN = 4  # safety cap per boss (3-4 rows/run) — protects against unattended cron burning API credits on a bad batch

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Content Queue column indices (0-based, matching sheet headers A–R)
_CQ = {
    "content_type":          0,   # A
    "video_type":            1,   # B
    "video_brief":           2,   # C
    "duration":              3,   # D
    "background_video_link": 4,   # E
    "channel":               5,   # F
    "playlist":              6,   # G
    "schedule_date":         7,   # H
    "schedule_time":         8,   # I
    "timezone":              9,   # J
    "generated_title":       10,  # K
    "generated_description": 11,  # L
    "generated_tags":        12,  # M
    "status":                13,  # N
    "video_id":              14,  # O
    "video_url":             15,  # P
    "music":                 16,  # Q — Drive link to a specific approved music track, or blank/"none" for narration-only
    "outro":                 17,  # R — Drive link to a clip appended to the end of the video, or blank/"none" for no outro
}

STATUS_PENDING     = "Pending"
STATUS_PROCESSING  = "Processing"
STATUS_TRANSFERRED = "Transferred"
STATUS_FAILED      = "Failed"


def _cell(row, col_key, default=""):
    idx = _CQ.get(col_key, -1)
    if idx < 0 or idx >= len(row):
        return default
    return row[idx].strip()


REQUIRED_FIELDS = {
    "content_type":  "Content Type",
    "video_brief":   "Video Brief",
    "channel":       "Channel",
    "schedule_date": "Schedule Date",
    "schedule_time": "Schedule Time",
    "timezone":      "Timezone",
}


def _validate(item) -> list[str]:
    """Return list of missing required field header names."""
    return [
        header
        for key, header in REQUIRED_FIELDS.items()
        if not item.get(key, "").strip()
    ]


def _parse_duration_minutes(duration_str: str) -> int:
    """Parse MM:SS or HH:MM:SS → total minutes (rounded up, minimum 1)."""
    try:
        parts = [int(p) for p in duration_str.strip().split(":")]
        if len(parts) == 3:
            total = parts[0] * 60 + parts[1] + (1 if parts[2] >= 30 else 0)
        elif len(parts) == 2:
            total = parts[0] + (1 if parts[1] >= 30 else 0)
        else:
            return 3
        return max(total, 1)
    except (ValueError, AttributeError):
        return 3


def _col_letter(idx):
    return chr(ord("A") + idx)


def _resolve_music_track(downloader, music_link, tmpdir):
    """Download the track at music_link, but only if its filename is actually
    approved (starts with SUNO MUSIC_) — protects against a wrong/unapproved
    link being pasted into the sheet. Returns None (narration-only) otherwise."""
    file_name = downloader.get_file_name(music_link)
    if not file_name.startswith(APPROVED_MUSIC_PREFIX):
        print(f"  Music   : link's file (\"{file_name or 'unknown'}\") isn't an approved '{APPROVED_MUSIC_PREFIX}' track — skipping for safety")
        return None
    dest = str(Path(tmpdir) / "music.mp3")
    if downloader.download(music_link, dest):
        print(f"  Music   : using {file_name}")
        return dest
    print(f"  Music   : download failed — proceeding narration-only")
    return None


class _QueueSheets:
    def __init__(self, spreadsheet_id, token_file):
        self.spreadsheet_id = spreadsheet_id
        creds = load_oauth_creds(token_file, SCOPES)
        self._svc = build("sheets", "v4", credentials=creds).spreadsheets()

    def get_pending(self):
        result = self._svc.values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{QUEUE_TAB}'!A1:R",
        ).execute()
        rows = result.get("values", [])
        if len(rows) < 2:
            return []

        pending = []
        for sheet_row, row in enumerate(rows[1:], start=2):
            row = row + [""] * (18 - len(row))
            if _cell(row, "status").lower() != STATUS_PENDING.lower():
                continue
            pending.append({
                "row_idx":               sheet_row,
                "content_type":          _cell(row, "content_type"),
                "video_type":            _cell(row, "video_type"),
                "video_brief":           _cell(row, "video_brief"),
                "duration":              _cell(row, "duration"),
                "background_video_link": _cell(row, "background_video_link"),
                "channel":               _cell(row, "channel"),
                "playlist":              _cell(row, "playlist"),
                "schedule_date":         _cell(row, "schedule_date"),
                "schedule_time":         _cell(row, "schedule_time"),
                "timezone":              _cell(row, "timezone"),
                "music":                 _cell(row, "music"),
                "outro":                 _cell(row, "outro"),
            })
        return pending

    def set_status(self, row_idx, status):
        self._svc.values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{QUEUE_TAB}'!{_col_letter(_CQ['status'])}{row_idx}",
            valueInputOption="RAW",
            body={"values": [[status]]},
        ).execute()

    def write_output(self, row_idx, title, description, tags_str, video_url):
        for col_key, value in [
            ("generated_title",       title),
            ("generated_description", description),
            ("generated_tags",        tags_str),
            ("video_url",             video_url),
        ]:
            self._svc.values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{QUEUE_TAB}'!{_col_letter(_CQ[col_key])}{row_idx}",
                valueInputOption="RAW",
                body={"values": [[value]]},
            ).execute()


def main():
    settings  = load_settings()
    channels  = load_channels(settings["channels_config"])
    token     = settings["credentials"]["master_token"]
    sheet_id  = settings["spreadsheet_id"]

    queue    = _QueueSheets(sheet_id, token)
    sheets   = SheetsReader(token_file=token, spreadsheet_id=sheet_id, settings=settings)
    downloader = DriveDownloader(token)
    uploader   = DriveUploader(token)

    pending = queue.get_pending()
    if not pending:
        print("Content Queue: nothing pending.")
        return

    if len(pending) > MAX_ROWS_PER_RUN:
        print(f"Content Queue: {len(pending)} row(s) pending — processing {MAX_ROWS_PER_RUN} this run, remainder deferred to next run.\n")
        pending = pending[:MAX_ROWS_PER_RUN]
    else:
        print(f"Content Queue: {len(pending)} row(s) to process.\n")

    for item in pending:
        row_idx = item["row_idx"]
        channel = item["channel"]
        brief   = item["video_brief"]

        print("─" * 60)
        print(f"  Row     : {row_idx}")
        print(f"  Channel : {channel}")
        print(f"  Brief   : {brief}")

        missing = _validate(item)
        if missing:
            for field in missing:
                print(f"  ERROR   : No metadata for '{field}'")
            queue.set_status(row_idx, STATUS_FAILED)
            continue

        if channel not in channels:
            logger.error("Row %d: channel '%s' not in channels.json", row_idx, channel)
            queue.set_status(row_idx, STATUS_FAILED)
            continue

        niche        = channels[channel].get("content_queue_niche", "general")
        duration_min = _parse_duration_minutes(item["duration"]) if item["duration"] else 3
        content_type = item["content_type"].lower()

        queue.set_status(row_idx, STATUS_PROCESSING)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Optional background video download
            background_path = None
            if item["background_video_link"]:
                bg_dest = str(Path(tmpdir) / "background.mp4")
                if downloader.download(item["background_video_link"], bg_dest):
                    background_path = bg_dest
                else:
                    logger.warning("Row %d: background video download failed — will use black background", row_idx)

            outro_path = None
            outro_link = item["outro"].strip()
            if outro_link and outro_link.lower() != "none":
                outro_dest = str(Path(tmpdir) / "outro.mp4")
                if downloader.download(outro_link, outro_dest):
                    outro_path = outro_dest
                else:
                    logger.warning("Row %d: outro clip download failed — proceeding without outro", row_idx)

            if content_type == "melody":
                logger.warning("Row %d: Melody content type coming next week — skipping", row_idx)
                queue.set_status(row_idx, STATUS_FAILED)
                continue
            elif content_type != "voiceover":
                logger.warning("Row %d: unknown content type '%s'", row_idx, content_type)
                queue.set_status(row_idx, STATUS_FAILED)
                continue

            music_path = None
            music_link = item["music"].strip()
            if music_link and music_link.lower() != "none":
                music_path = _resolve_music_track(downloader, music_link, tmpdir)

            try:
                music_note = " + music" if music_path else ""
                print(f"  Pipeline: Generating script + voiceover + assembly{music_note} ({duration_min} min)...")
                result = run_phase2a(
                    title=brief,
                    channel_name=channel,
                    niche=niche,
                    background_video_path=background_path,
                    output_dir=tmpdir,
                    duration_minutes=duration_min,
                    music_path=music_path,
                )
                print(f"  Pipeline: Complete — \"{result['title']}\"")
            except Exception as exc:
                logger.error("Row %d: pipeline failed: %s", row_idx, exc)
                queue.set_status(row_idx, STATUS_FAILED)
                continue

            final_video_path = result["final_video_path"]
            if outro_path:
                try:
                    print(f"  Outro   : Appending outro clip...")
                    stitched_path = str(Path(tmpdir) / "final_with_outro.mp4")
                    final_video_path = append_clips(final_video_path, [outro_path], stitched_path)
                    print(f"  Outro   : Appended")
                except Exception as exc:
                    logger.warning("Row %d: outro append failed (%s) — uploading without outro", row_idx, exc)
                    final_video_path = result["final_video_path"]

            try:
                print(f"  Drive   : Uploading assembled video...")
                drive_link = uploader.upload(final_video_path, GENERATED_FOLDER_ID)
                print(f"  Drive   : {drive_link}")
            except Exception as exc:
                logger.error("Row %d: Drive upload failed: %s", row_idx, exc)
                queue.set_status(row_idx, STATUS_FAILED)
                continue

        tags_str = ", ".join(result["tags"]) if isinstance(result["tags"], list) else result["tags"]

        try:
            sheets.append_row(channel, {
                "title":        result["title"],
                "description":  result["description"],
                "tags":         tags_str,
                "video_link":   drive_link,
                "playlist":     item["playlist"],
                "schedule_date": item["schedule_date"],
                "schedule_time": item["schedule_time"],
                "timezone_col": item["timezone"],
                "status":       settings["status"]["ready"],
            })
            print(f"  Sheet   : Row added to '{channel}' tab → Ready For Upload")
        except Exception as exc:
            logger.error("Row %d: channel tab write failed: %s", row_idx, exc)
            queue.set_status(row_idx, STATUS_FAILED)
            continue

        queue.write_output(row_idx, result["title"], result["description"], tags_str, drive_link)
        queue.set_status(row_idx, STATUS_TRANSFERRED)
        print(f"  Status  : Transferred ✓")

    print("\n" + "═" * 60)
    print("  Content Queue: run complete.")
    print("═" * 60)


if __name__ == "__main__":
    main()
