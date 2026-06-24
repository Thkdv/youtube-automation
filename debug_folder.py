"""One-off debug: list every file the API can see in the watch folder, no filtering."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.config_loader import load_settings, load_channels
from modules.auth import load_oauth_creds
from googleapiclient.discovery import build

settings = load_settings()
channels = load_channels(settings["channels_config"])
master_token = settings["credentials"]["master_token"]

creds = load_oauth_creds(master_token, ["https://www.googleapis.com/auth/drive"])
svc = build("drive", "v3", credentials=creds)

about = svc.about().get(fields="user").execute()
print(f"master_token.json is authenticated as: {about['user']['emailAddress']}\n")

first_channel = next((c for c in channels.values() if c.get("watch_folder_id")), None)
if not first_channel:
    print("No channel with watch_folder_id configured.")
    sys.exit(1)

folder_id = first_channel["watch_folder_id"]
print(f"Checking folder ID: {folder_id}\n")

try:
    folder_meta = svc.files().get(
        fileId=folder_id, fields="id, name, mimeType", supportsAllDrives=True
    ).execute()
    print(f"Folder found: '{folder_meta['name']}' (type: {folder_meta['mimeType']})\n")
except Exception as exc:
    print(f"COULD NOT ACCESS FOLDER: {exc}\n")

result = svc.files().list(
    q=f"'{folder_id}' in parents and trashed = false",
    fields="files(id, name, mimeType, createdTime)",
    supportsAllDrives=True,
    includeItemsFromAllDrives=True,
).execute()

files = result.get("files", [])
print(f"Files found in folder: {len(files)}\n")
for f in files:
    print(f"  - {f['name']}  (type: {f['mimeType']})")
