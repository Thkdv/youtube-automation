#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 main.py >> "$HOME/logs/yt-uploader.log" 2>&1
