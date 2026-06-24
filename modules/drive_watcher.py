import json
import logging
from pathlib import Path

from googleapiclient.discovery import build

from .auth import load_oauth_creds

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]

VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv")
PROCESSED_FILE = Path(__file__).parent.parent / "data" / "watched_files.json"


class DriveWatcher:
    def __init__(self, token_file):
        creds = load_oauth_creds(token_file, SCOPES)
        self._svc = build("drive", "v3", credentials=creds)
        self._processed = self._load_processed()

    def _load_processed(self):
        if PROCESSED_FILE.exists():
            try:
                return set(json.loads(PROCESSED_FILE.read_text()))
            except (json.JSONDecodeError, TypeError):
                return set()
        return set()

    def _save_processed(self):
        PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROCESSED_FILE.write_text(json.dumps(sorted(self._processed)))

    def scan_folder(self, folder_id):
        """Returns list of new (video_file, metadata_file) dicts not yet processed."""
        try:
            result = self._svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="files(id, name, mimeType, createdTime)",
                orderBy="createdTime",
                pageSize=200,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
        except Exception as exc:
            logger.error("Failed to list folder %s: %s", folder_id, exc)
            return []

        files = result.get("files", [])
        videos, metadata = {}, {}

        for f in files:
            stem = Path(f["name"]).stem
            lower = f["name"].lower()
            if lower.endswith(VIDEO_EXTENSIONS):
                videos[stem] = f
            elif lower.endswith(".json"):
                metadata[stem] = f

        pairs = []
        for stem, video_file in videos.items():
            if video_file["id"] in self._processed:
                continue
            if stem not in metadata:
                logger.warning("Video '%s' has no matching .json metadata file — skipping", video_file["name"])
                continue
            pairs.append({"video": video_file, "metadata": metadata[stem]})

        return pairs

    def read_metadata(self, metadata_file):
        """Download and parse a metadata JSON file. Returns None on failure."""
        try:
            content = self._svc.files().get_media(
                fileId=metadata_file["id"], supportsAllDrives=True
            ).execute()
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("Malformed JSON in '%s': %s", metadata_file["name"], exc)
            return None
        except Exception as exc:
            logger.error("Failed to read metadata '%s': %s", metadata_file["name"], exc)
            return None

    def file_link(self, video_file):
        return f"https://drive.google.com/file/d/{video_file['id']}/view"

    def mark_processed(self, video_file):
        self._processed.add(video_file["id"])
        self._save_processed()
