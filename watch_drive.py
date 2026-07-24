"""
Drive folder watcher test script.

Default (dry run): detects new video + metadata pairs and prints what row
WOULD be created. Does not write to the sheet or mark files as processed.

--live: actually creates the sheet row and marks the file as processed.
        Still does NOT trigger any upload — that happens on the next
        normal run of main.py, exactly like a manually-typed row.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.config_loader import load_settings, load_channels
from modules.drive_watcher import DriveWatcher
from modules.sheets_reader import SheetsReader


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Actually create sheet rows and mark files processed")
    args = parser.parse_args()

    settings = load_settings()
    channels = load_channels(settings["channels_config"])
    master_token = settings["credentials"]["master_token"]

    watcher = DriveWatcher(master_token)
    sheets = SheetsReader(
        token_file=master_token,
        spreadsheet_id=settings["spreadsheet_id"],
        settings=settings,
    ) if args.live else None

    mode_label = "LIVE — creating real sheet rows" if args.live else "DRY RUN — no sheet writes, no uploads"
    print("=" * 60)
    print(f"  DRIVE FOLDER WATCHER — {mode_label}")
    print("=" * 60)

    for channel_name, cfg in channels.items():
        folder_id = cfg.get("watch_folder_id")
        if not folder_id:
            print(f"\n[{channel_name}] No watch_folder_id configured — skipping")
            continue

        pairs = watcher.scan_folder(folder_id)
        if not pairs:
            print(f"\n[{channel_name}] No new files detected")
            continue

        print(f"\n[{channel_name}] Found {len(pairs)} new file pair(s):")
        for pair in pairs:
            video_file = pair["video"]
            metadata = watcher.read_metadata(pair["metadata"])

            print(f"\n  Video file : {video_file['name']}")
            if metadata is None:
                print(f"  Result     : SKIPPED (malformed or unreadable metadata JSON)")
                continue

            row_values = {
                "title":         metadata.get("title", ""),
                "description":   metadata.get("description", ""),
                "tags":          ", ".join(metadata.get("tags", [])) if isinstance(metadata.get("tags"), list) else metadata.get("tags", ""),
                "video_link":    watcher.file_link(video_file),
                "playlist":      metadata.get("playlist", ""),
                "schedule_date": metadata.get("schedule_date", ""),
                "schedule_time": metadata.get("schedule_time", ""),
                "status":        settings["status"]["ready"],
                "timezone_col":  settings["timezone"],
            }

            if args.live:
                new_row = sheets.append_row(channel_name, row_values)
                watcher.mark_processed(video_file)
                print(f"  Result     : Row {new_row} created in '{channel_name}' tab, status set to Ready For Upload")
            else:
                print(f"  Would create row:")
                for k, v in row_values.items():
                    print(f"    {k:<13}: {v or '(missing)'}")
                print(f"  (dry run — file NOT marked as processed, nothing written)")

    print("\n" + "=" * 60)
    print("  Done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
