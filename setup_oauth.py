"""
Run once per channel to generate its YouTube OAuth token.

Usage:
    python setup_oauth.py --channel "My Channel" --channel-id UCxxxxxxxxxxxxxxxxxxxxxxx
    python setup_oauth.py --master   # Sheets + Drive token only
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
MASTER_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CLIENT_SECRETS = Path("credentials/client_secrets.json")
OAUTH_DIR = Path("credentials/oauth")


def safe_filename(name):
    return re.sub(r"[^\w\-().]", "_", name)


def run_flow(scopes, port=8090):
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), scopes)
    creds = flow.run_local_server(port=port, open_browser=True)
    return creds


def save_token(creds, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(creds.to_json())
    print(f"Token saved: {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", help="Channel tab name (must match sheet tab)")
    parser.add_argument("--channel-id", help="YouTube channel ID (UCxxx...)")
    parser.add_argument("--master", action="store_true", help="Generate master token (Sheets+Drive)")
    args = parser.parse_args()

    if not CLIENT_SECRETS.exists():
        print(f"ERROR: {CLIENT_SECRETS} not found.")
        sys.exit(1)

    OAUTH_DIR.mkdir(parents=True, exist_ok=True)

    if args.master:
        print("Authorising master token (Sheets + Drive)...")
        creds = run_flow(MASTER_SCOPES)
        save_token(creds, OAUTH_DIR / "master_token.json")

    elif args.channel:
        name = args.channel
        filename = safe_filename(name) + ".json"
        out_path = OAUTH_DIR / filename
        print(f"Authorising YouTube token for: {name}")
        print(f"Will save to: {out_path}")
        creds = run_flow(YOUTUBE_SCOPES)
        save_token(creds, out_path)
        print(f"\nAdd this to config/channels.json:")
        print(json.dumps({name: {"channel_id": args.channel_id or "UC...", "token_file": f"credentials/oauth/{filename}"}}, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
