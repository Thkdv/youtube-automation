import logging
import sys
import time
from datetime import datetime, timezone as _tz

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from tqdm import tqdm

from .auth import load_oauth_creds

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube"]
CHUNK_SIZE = 128 * 1024 * 1024  # 128 MB


class YouTubeUploader:
    def __init__(self, token_file):
        creds = load_oauth_creds(token_file, SCOPES)
        self._svc = build("youtube", "v3", credentials=creds)

    def upload_video(self, file_path, title, description, tags, publish_at=None, category_id="22", made_for_kids=False):
        import os
        now_utc = datetime.now(_tz.utc)
        file_size = os.path.getsize(file_path)

        if publish_at and publish_at.tzinfo and publish_at > now_utc:
            privacy_status = "private"
            publish_at_str = publish_at.isoformat()
            logger.info("Scheduling for %s", publish_at_str)
        else:
            privacy_status = "public"
            publish_at_str = None
            logger.info("Publishing immediately")

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:30],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": made_for_kids,
                **({"publishAt": publish_at_str} if publish_at_str else {}),
            },
        }

        media = MediaFileUpload(file_path, chunksize=CHUNK_SIZE, resumable=True, mimetype="video/mp4")
        interactive = sys.stdout.isatty()

        try:
            req = self._svc.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None

            with tqdm(
                total=file_size,
                unit="B",
                unit_scale=True,
                desc="  Uploading ",
                dynamic_ncols=True,
                disable=not interactive,
            ) as pbar:
                last_progress = 0
                while response is None:
                    retries, backoff = 0, 2
                    while retries <= 3:
                        try:
                            status, response = req.next_chunk()
                            if status:
                                current = int(status.resumable_progress)
                                pbar.update(current - last_progress)
                                last_progress = current
                                pct = int(status.progress() * 100)
                                if not interactive:
                                    logger.info("YouTube upload: %d%%", pct)
                            break
                        except HttpError as exc:
                            retries += 1
                            if retries > 3:
                                raise
                            # Rate limited or quota — use long backoff (5m, 10m, 20m)
                            if exc.resp.status in (429, 403):
                                long_wait = backoff * 60
                                logger.warning("Rate limited, waiting %ds (attempt %d/3)...", long_wait, retries)
                                time.sleep(long_wait)
                            else:
                                logger.warning("Chunk error, retrying in %ds: %s", backoff, exc)
                                time.sleep(backoff)
                            backoff *= 2
                        except Exception as exc:
                            retries += 1
                            if retries > 3:
                                raise
                            logger.warning("Chunk error, retrying in %ds: %s", backoff, exc)
                            time.sleep(backoff)
                            backoff *= 2

            video_id = response["id"]
            logger.info("Uploaded: https://youtu.be/%s", video_id)
            return video_id

        except HttpError as exc:
            logger.error("YouTube upload failed: %s", exc)
            return None

    def get_or_create_playlist(self, name):
        existing = self._find_playlist(name)
        if existing:
            return existing
        try:
            pl = self._svc.playlists().insert(
                part="snippet,status",
                body={"snippet": {"title": name}, "status": {"privacyStatus": "public"}},
            ).execute()
            logger.info("Created playlist '%s' (%s)", name, pl["id"])
            return pl["id"]
        except HttpError as exc:
            logger.error("Failed to create playlist '%s': %s", name, exc)
            return None

    def add_to_playlist(self, video_id, playlist_id):
        for attempt in range(1, 4):
            try:
                self._svc.playlistItems().insert(
                    part="snippet",
                    body={"snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }},
                ).execute()
                logger.info("Added to playlist %s", playlist_id)
                return True
            except HttpError as exc:
                if exc.resp.status == 404 and attempt < 3:
                    logger.warning("Video not ready yet, retrying in 15s (attempt %d/3)...", attempt)
                    time.sleep(15)
                    continue
                logger.error("Add to playlist failed: %s", exc)
                return False
        return False

    def _find_playlist(self, name):
        page_token = None
        while True:
            kwargs = dict(part="snippet", mine=True, maxResults=50)
            if page_token:
                kwargs["pageToken"] = page_token
            try:
                resp = self._svc.playlists().list(**kwargs).execute()
            except HttpError as exc:
                logger.error("Playlist list failed: %s", exc)
                return None
            for item in resp.get("items", []):
                if item["snippet"]["title"].strip().lower() == name.strip().lower():
                    return item["id"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return None
