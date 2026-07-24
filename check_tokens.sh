#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
venv/bin/python check_tokens.py >> "$HOME/logs/atlas-uploader.log" 2>&1
