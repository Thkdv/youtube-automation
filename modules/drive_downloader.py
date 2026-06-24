import logging
import re
import sys
import time

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from tqdm import tqdm

from .auth import load_oauth_creds

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]
CHUNK_SIZE = 128 * 1024 * 1024  # 128 MB


class DriveDownloader:
    def __init__(self, token_file):
        creds = load_oauth_creds(token_file, SCOPES)
        self._svc = build("drive", "v3", credentials=creds)

    def download(self, drive_url, dest_path) -> bool:
        file_id = self._extract_id(drive_url)
        if not file_id:
            logger.error("Invalid Drive URL: %s", drive_url)
            return False

        # Get file size for progress bar
        try:
            meta = self._svc.files().get(
                fileId=file_id, fields="size,name", supportsAllDrives=True
            ).execute()
            file_size = int(meta.get("size", 0))
        except Exception:
            file_size = 0

        interactive = sys.stdout.isatty()
        backoff = 5

        for attempt in range(1, 4):
            try:
                request = self._svc.files().get_media(fileId=file_id, supportsAllDrives=True)
                with open(dest_path, "wb") as f:
                    downloader = MediaIoBaseDownload(f, request, chunksize=CHUNK_SIZE)
                    done = False
                    with tqdm(
                        total=file_size or 100,
                        unit="B" if file_size else "%",
                        unit_scale=bool(file_size),
                        desc="  Downloading",
                        dynamic_ncols=True,
                        disable=not interactive,
                    ) as pbar:
                        last_progress = 0
                        while not done:
                            status, done = downloader.next_chunk()
                            if status:
                                if file_size:
                                    current = int(status.resumable_progress)
                                    pbar.update(current - last_progress)
                                    last_progress = current
                                else:
                                    pct = int(status.progress() * 100)
                                    pbar.update(pct - last_progress)
                                    last_progress = pct
                                if not interactive:
                                    logger.info("Drive download: %d%%", int(status.progress() * 100))
                logger.info("Drive download complete")
                return True
            except Exception as exc:
                if attempt == 3:
                    logger.error("Drive download failed (id=%s): %s", file_id, exc)
                    return False
                logger.warning("Download interrupted, retrying in %ds: %s", backoff, exc)
                time.sleep(backoff)
                backoff *= 2
        return False

    @staticmethod
    def _extract_id(url):
        m = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
        if m:
            return m.group(1)
        m = re.search(r"id=([a-zA-Z0-9-_]+)", url)
        return m.group(1) if m else None
