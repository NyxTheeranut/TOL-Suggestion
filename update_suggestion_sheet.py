#!/usr/bin/env python3
"""
Pushes the curated village/building records (aggregate_suggestion.build_output())
into the Google Sheet the TOL Suggestion dashboard's Apps Script reads from --
same normalized-tabs-over-JSON-blob approach as TOL Tracker (sheet_schema.py
does the flatten, chunk_tabs keeps individual POSTs under the size that makes
Apps Script choke).

Unlike TOL Tracker, there's no month-by-month history to protect here --
Villages/Buildings are a full snapshot of the current workbooks, so each run
just overwrites them outright. It deliberately never touches CalendarTheme /
CalendarPlan -- those tabs belong to the live page (saveCalendarTheme /
saveCalendarDay), and omitting them from this payload is what keeps this
script from ever clobbering a team's in-progress monthly plan.

Run this any time a fresh export lands in data/ (or double-click
"Update TOL Suggestion.command" in the Dashboard folder's Launchers/).
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DASHBOARD_DIR = HERE.parent
sys.path.insert(0, str(HERE))
import aggregate_suggestion  # noqa: E402
import sheet_schema  # noqa: E402

# Keep in sync with DEFAULT_SYNC_URL in index.html.
SYNC_URL = "https://script.google.com/macros/s/AKfycbxto0D-v5bV2zRRI70xug8I9Q85lAj1iqxAaGB30sVsjYYhfkgOFYQHrlHLin2-z77lGg/exec"

# Shared with other Dashboard projects rather than a dedicated
# suggestion_sync_secret.txt -- this is the file the user pointed the Apps
# Script's SYNC_SECRET script property at.
SYNC_SECRET_FILE = DASHBOARD_DIR / "Config" / "sync_secret.txt"


def post(sync_secret, action, **fields):
    payload = json.dumps({"action": action, "secret": sync_secret, **fields}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        SYNC_URL, data=payload, method="POST",
        headers={"Content-Type": "text/plain;charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            body = res.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{action} failed: HTTP {e.code}\n{e.read().decode('utf-8', 'replace')[:500]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"{action} failed: {e.reason}")
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        raise SystemExit(
            f"{action} failed: response wasn't JSON (the Web App URL may need to be "
            "redeployed with \"Who has access: Anyone\").\n"
            f"First 300 chars of response:\n{body[:300]}"
        )
    if not result.get("ok"):
        raise SystemExit(f"{action} failed: {result.get('error')}")
    return result


def chunk_tabs(tabs, max_bytes=400_000):
    """Same greedy bin-packing as TOL Tracker's update_bb_sheet.py -- a
    single large POST to the Apps Script Web App comes back as an opaque
    Google error page rather than a JSON error from our own code, so
    everything stays well under whatever the real ceiling is."""
    chunk, chunk_size = {}, 0
    for name, t in tabs.items():
        header, rows = t["header"], t["rows"]
        t_size = len(json.dumps(t, ensure_ascii=False))
        if t_size <= max_bytes:
            if chunk and chunk_size + t_size > max_bytes:
                yield chunk
                chunk, chunk_size = {}, 0
            chunk[name] = t
            chunk_size += t_size
            continue

        if chunk:
            yield chunk
            chunk, chunk_size = {}, 0
        avg_row_size = t_size / max(len(rows), 1)
        rows_per_piece = max(int(max_bytes / avg_row_size), 1)
        for i in range(0, len(rows), rows_per_piece):
            piece = {"header": header, "rows": rows[i:i + rows_per_piece]}
            if i > 0:
                piece["append"] = True
            yield {name: piece}
    if chunk:
        yield chunk


PROGRESS_TOTAL_STEPS = 3


def progress(step, label, width=28):
    filled = int(width * step / PROGRESS_TOTAL_STEPS)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * step / PROGRESS_TOTAL_STEPS)
    print(f"\n[{bar}] {pct:3d}%  Step {step}/{PROGRESS_TOTAL_STEPS}: {label}")


def upload_bar(done_bytes, total_bytes, width=28):
    frac = min(done_bytes / total_bytes, 1) if total_bytes else 1
    filled = int(width * frac)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * frac)
    print(f"\r  [{bar}] {pct:3d}%  {done_bytes / 1e6:.2f}/{total_bytes / 1e6:.2f} MB uploaded",
          end="", flush=True)


def main():
    if not SYNC_SECRET_FILE.exists():
        raise SystemExit(
            f"Not found: {SYNC_SECRET_FILE}\n"
            "Create it containing the same value as the SYNC_SECRET Script "
            "Property in the Apps Script project, with no extra whitespace."
        )
    sync_secret = SYNC_SECRET_FILE.read_text(encoding="utf-8").strip()

    if SYNC_URL.startswith("REPLACE_WITH"):
        raise SystemExit(
            "SYNC_URL at the top of this script still needs to be set to "
            "your Apps Script Web App URL -- see this repo's README."
        )

    progress(1, "Curating villages + buildings")
    print("Curating villages + buildings from the raw workbooks...")
    out = aggregate_suggestion.build_output()
    out["meta"]["syncedAt"] = datetime.now(timezone.utc).isoformat()
    print(f"  {out['meta']['villageCount']} villages, {out['meta']['buildingCount']} buildings")

    progress(2, "Preparing tabs to upload")
    tabs = sheet_schema.flatten(out)
    total_rows = sum(len(t["rows"]) for t in tabs.values())
    print(f"Flattened into {len(tabs)} tabs, {total_rows} rows total:")
    for name, t in tabs.items():
        print(f"  {name:<12} {len(t['rows']):>6} rows")

    payload_preview = json.dumps(tabs, ensure_ascii=False)
    total_bytes = len(payload_preview.encode("utf-8"))
    print(f"Upload size: {total_bytes / 1e6:.2f} MB")

    progress(3, "Uploading to Google Sheet")
    total_tabs = 0
    total_rows = 0
    done_bytes = 0
    for i, chunk in enumerate(chunk_tabs(tabs), 1):
        chunk_json = json.dumps(chunk, ensure_ascii=False)
        chunk_mb = len(chunk_json) / 1e6
        print(f"  chunk {i}: {list(chunk.keys())} ({chunk_mb:.2f} MB)")
        result = post(sync_secret, "syncData", tabs=chunk)
        total_tabs += result.get("tabs", 0)
        total_rows += result.get("rows", 0)
        done_bytes += len(chunk_json.encode("utf-8"))
        upload_bar(done_bytes, total_bytes)
    print()
    print(f"Done -- {total_rows} rows synced across {total_tabs} tabs.")


if __name__ == "__main__":
    main()
