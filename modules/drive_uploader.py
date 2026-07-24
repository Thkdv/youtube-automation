"""
Drive uploader — uploads a local file to a specified Drive folder.
Returns a shareable Drive link.
"""
import logging
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .auth import load_oauth_creds

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveUploader:
    def __init__(self, token_file):
        creds = load_oauth_creds(token_file, SCOPES)
        self._svc = build("drive", "v3", credentials=creds)

    def upload(self, file_path: str, folder_id: str, filename: str = None) -> str:
        name = filename or Path(file_path).name
        metadata = {"name": name, "parents": [folder_id]}
        media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=True)

        logger.info("Uploading %s to Drive folder %s...", name, folder_id)
        result = self._svc.files().create(
            body=metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        ).execute()

        file_id = result["id"]
        link = f"https://drive.google.com/file/d/{file_id}/view?usp=drive_link"
        logger.info("Drive upload complete: %s", link)
        return link
