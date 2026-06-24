import logging
from datetime import datetime

import pytz
from googleapiclient.discovery import build

from .auth import load_oauth_creds

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_DATE_FORMATS = [
    "%Y-%m-%d",
    "%d/%m/%Y",   # UK/EU first to avoid ambiguity with US
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d/%m/%y",
    "%m/%d/%y",
]


def _parse_date(s):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _col_letter(idx):
    result = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


class SheetsReader:
    def __init__(self, token_file, spreadsheet_id, settings):
        self.spreadsheet_id = spreadsheet_id
        self.settings = settings
        self.col_cfg = {k: v.strip() for k, v in settings["columns"].items()}
        creds = load_oauth_creds(token_file, SCOPES)
        self._svc = build("sheets", "v4", credentials=creds).spreadsheets()
        self._header_cache = {}

    def get_sheet_tabs(self):
        meta = self._svc.get(spreadsheetId=self.spreadsheet_id).execute()
        return [s["properties"]["title"] for s in meta["sheets"]]

    def get_pending_uploads(self, tab_name):
        """Return (pending, skipped) where skipped is a list of {row, status} dicts."""
        try:
            result = self._svc.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{tab_name}'!A1:Z",
            ).execute()
        except Exception as exc:
            logger.error("Failed to read tab '%s': %s", tab_name, exc)
            return [], []

        rows = result.get("values", [])
        if not rows:
            return [], []

        headers = [h.strip() for h in rows[0]]
        self._header_cache[tab_name] = headers
        col = self._col_index_map(headers)
        tz = pytz.timezone(self.settings["timezone"])

        pending, skipped = [], []
        for sheet_row, row in enumerate(rows[1:], start=2):
            row = self._pad(row, len(headers))

            if self._cell(row, col, "completed").strip():
                continue

            status_val = self._cell(row, col, "status").strip()
            ready_val = self.settings["status"]["ready"]
            if status_val.lower() != ready_val.lower():
                if status_val:
                    skipped.append({"row": sheet_row, "status": status_val})
                continue

            date_str = self._cell(row, col, "schedule_date")
            time_str = self._cell(row, col, "schedule_time")
            scheduled_at = self._parse_schedule(date_str, time_str, tz, tab_name, sheet_row)
            if scheduled_at is None:
                reason = f"bad date '{date_str}'" if not _parse_date(date_str) else f"bad time '{time_str}'"
                skipped.append({"row": sheet_row, "status": f"Invalid schedule ({reason})"})
                continue

            pending.append({
                "row_idx":      sheet_row,
                "tab_name":     tab_name,
                "title":        self._cell(row, col, "title"),
                "description":  self._cell(row, col, "description"),
                "tags":         self._cell(row, col, "tags"),
                "video_link":   self._cell(row, col, "video_link"),
                "playlist":     self._cell(row, col, "playlist"),
                "scheduled_at": scheduled_at,
            })

        return pending, skipped

    def update_cell(self, tab_name, row_idx, col_key, value):
        headers = self._get_headers(tab_name)
        col_name = self.col_cfg.get(col_key, "")
        for idx, h in enumerate(headers):
            if h.strip().lower() == col_name.lower():
                cell = f"'{tab_name}'!{_col_letter(idx)}{row_idx}"
                self._svc.values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=cell,
                    valueInputOption="RAW",
                    body={"values": [[value]]},
                ).execute()
                return
        logger.warning("[%s] Column '%s' not found — skipping write", tab_name, col_name)

    def update_completed(self, tab_name, row_idx, value):
        self.update_cell(tab_name, row_idx, "completed", value)

    def update_video_id(self, tab_name, row_idx, video_id):
        self.update_cell(tab_name, row_idx, "video_id", video_id)

    def append_row(self, tab_name, values_by_col_key):
        """Append a new row. values_by_col_key maps column keys (e.g. 'title') to values.
        Columns not present in values_by_col_key are left blank. Returns the new row number.

        Uses an explicit row/column range rather than values().append(), since append()
        auto-detects the target table by scanning for existing data and can misalign
        columns if there is any stray data elsewhere in the sheet."""
        headers = self._get_headers(tab_name)
        colname_to_key = {v.strip().lower(): k for k, v in self.col_cfg.items()}

        row = []
        for h in headers:
            key = colname_to_key.get(h.strip().lower())
            row.append(values_by_col_key.get(key, "") if key else "")

        end_col = _col_letter(len(headers) - 1)
        existing = self._svc.values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab_name}'!A:{end_col}",
        ).execute()
        # Last row with data in ANY column, not just column A — a row that is
        # blank in one column but has data in another must not be overwritten.
        new_row_idx = len(existing.get("values", [])) + 1

        self._svc.values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab_name}'!A{new_row_idx}:{end_col}{new_row_idx}",
            valueInputOption="RAW",
            body={"values": [row]},
        ).execute()

        return new_row_idx

    # ── private ───────────────────────────────────────────────────────────────

    def _get_headers(self, tab_name):
        if tab_name in self._header_cache:
            return self._header_cache[tab_name]
        result = self._svc.values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab_name}'!1:1",
        ).execute()
        headers = [h.strip() for h in result.get("values", [[]])[0]]
        self._header_cache[tab_name] = headers
        return headers

    def _col_index_map(self, headers):
        lower = [h.lower() for h in headers]
        result = {}
        for key, col_name in self.col_cfg.items():
            try:
                result[key] = lower.index(col_name.lower())
            except ValueError:
                result[key] = -1
        return result

    @staticmethod
    def _pad(row, length):
        return row + [""] * (length - len(row))

    @staticmethod
    def _cell(row, col, key):
        idx = col.get(key, -1)
        if idx < 0 or idx >= len(row):
            return ""
        return row[idx]

    def _parse_schedule(self, date_str, time_str, tz, tab, row):
        date_dt = _parse_date(date_str)
        if not date_dt:
            logger.warning("[%s] Row %d: unrecognised date '%s'", tab, row, date_str)
            return None
        try:
            parts = [int(x) for x in time_str.strip().split(":")]
            naive = date_dt.replace(hour=parts[0], minute=parts[1], second=0, microsecond=0)
            return tz.localize(naive)
        except (ValueError, IndexError, AttributeError):
            logger.warning("[%s] Row %d: unrecognised time '%s'", tab, row, time_str)
            return None
