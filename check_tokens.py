"""
OAuth token status — shows a countdown to the 7-day Testing-mode refresh
token cap for each token, plus a live check against Google for certainty.

The countdown is an ESTIMATE based on each token file's last-modified time
(i.e. the last time setup_oauth.py was run for it) — Google doesn't expose
a refresh token's actual expiry, so this assumes exactly 7 days from
issuance, same as the app's current OAuth consent screen status (External +
Testing). The live check is the ground truth; the countdown is early warning
so tokens can be refreshed before they die mid-run.

Cron-friendly: exits 1 and prints a highly visible banner if any token is
broken or within WARNING_THRESHOLD_DAYS of the estimated cap, so a scheduled
run surfaces clearly in the log rather than relying on someone remembering to
check manually. This doesn't (can't) automate the actual re-authorization —
that always needs an interactive browser login — it only automates catching
that one's due.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.auth import load_oauth_creds
from modules.config_loader import load_settings, load_channels

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
MASTER_SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
TESTING_CAP_DAYS = 7
WARNING_THRESHOLD_DAYS = 2  # flag a token once it's within this many days of the estimated cap


def _fmt_delta(td):
    days = td.days
    hours = td.seconds // 3600
    return f"{days}d {hours}h"


def _check(name, token_file, scopes):
    """Returns (status_line, needs_attention)."""
    path = Path(token_file)
    if not path.exists():
        return f"  {name:<18} MISSING           ({token_file})", True

    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    remaining = timedelta(days=TESTING_CAP_DAYS) - age
    countdown = f"~{_fmt_delta(remaining)} left" if remaining.total_seconds() > 0 else "estimated cap passed"

    try:
        load_oauth_creds(str(path), scopes)
        live = "OK"
    except Exception:
        live = "BROKEN"

    needs_attention = live == "BROKEN" or remaining <= timedelta(days=WARNING_THRESHOLD_DAYS)
    return f"  {name:<18} {live:<8} last authorized {_fmt_delta(age)} ago — {countdown}", needs_attention


def main():
    settings = load_settings()
    channels = load_channels(settings["channels_config"])

    print("OAuth Token Status  (estimate assumes 7-day Testing-mode cap)")
    print("=" * 68)

    results = [_check("master_token", settings["credentials"]["master_token"], MASTER_SCOPES)]
    for name, cfg in channels.items():
        results.append(_check(name, cfg.get("token_file", ""), YOUTUBE_SCOPES))

    for line, _ in results:
        print(line)
    print("=" * 68)
    print("If 'estimated cap passed' but status is still OK, the estimate was")
    print("pessimistic — trust the live status. If BROKEN, re-run setup_oauth.py.")

    if any(needs_attention for _, needs_attention in results):
        print()
        print("!" * 68)
        print("! ACTION NEEDED: one or more tokens are broken or close to the cap.")
        print("! Re-authorize locally (python setup_oauth.py ...) and push to the VM.")
        print("!" * 68)
        sys.exit(1)


if __name__ == "__main__":
    main()
