import json
import os
from pathlib import Path

import yaml

_BASE = Path(os.environ.get("BASE_DIR", Path(__file__).parent.parent))


def _resolve(path):
    p = Path(path)
    return str(p if p.is_absolute() else _BASE / p)


def load_settings():
    with open(_BASE / "config" / "settings.yaml") as f:
        s = yaml.safe_load(f)
    s["credentials"]["master_token"] = _resolve(s["credentials"]["master_token"])
    s["credentials"]["client_secrets"] = _resolve(s["credentials"]["client_secrets"])
    s["channels_config"] = _resolve(s["channels_config"])
    return s


def load_channels(path):
    with open(path) as f:
        channels = json.load(f)
    for ch in channels.values():
        ch["token_file"] = _resolve(ch["token_file"])
    return channels
