#!/usr/bin/env python3
"""
Uploads the street ("soi") data of the big villages to the `Soi` tab of the Google Sheet, so the page can split a
village's sois between the DRS who work that day -- after sign-in only.

Why the Sheet and not the repo: the repo behind GitHub Pages is public. The street geometry is derived from our own
customer positions (it never contains a customer's name, address or exact location, but it is still our data), so it is
served by the Apps Script only to signed-in, allow-listed users (action getSoi) and is never committed here.

Usage (from the repo folder):
    python3 sync/upload_soi.py assets/soi-data.js          # the file the soi pipeline produced
    python3 sync/upload_soi.py path/to/soi_data.json

It reads window.SOI_DATA = {"villages": [...]} (.js) or {"villages": [...]} (.json), keeps only what the page needs
(gate, centre, streets with house counts and line points), and sends a few villages per request. Re-running replaces
every village in the file. Needs the same Config/sync_secret.txt as update_suggestion_sheet.py.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DASHBOARD_DIR = HERE.parent.parent
# Keep in sync with DEFAULT_SYNC_URL in index.html and update_suggestion_sheet.py.
SYNC_URL = "https://script.google.com/macros/s/AKfycbxto0D-v5bV2zRRI70xug8I9Q85lAj1iqxAaGB30sVsjYYhfkgOFYQHrlHLin2-z77lGg/exec"
SYNC_SECRET_FILE = DASHBOARD_DIR / "Config" / "sync_secret.txt"
MAX_REQUEST_BYTES = 300_000


def load_villages(path):
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith(".js"):
        text = text[text.index("{"):].rstrip().rstrip(";")
    data = json.loads(text)
    return data["villages"] if isinstance(data, dict) else data


def slim(v):
    """Only what the page's street splitter reads."""
    return {
        "id": str(v["id"]), "conf": v.get("conf"), "unsure": v.get("unsure", 0),
        "gate": v["gate"], "gatePt": v["gatePt"], "center": v["center"],
        "segs": [{"a": s["a"], "b": s["b"], "n": s.get("n", ""), "len": s["len"], "tot": s["tot"], "tru": s["tru"], "pts": s["pts"], "u": s.get("u", 0)} for s in v["segs"]],
    }


def post(secret, payload):
    body = json.dumps({"action": "putSoi", "secret": secret, **payload}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        SYNC_URL, data=body, method="POST",
        headers={"Content-Type": "text/plain;charset=utf-8", "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            out = json.loads(res.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        raise SystemExit(f"upload failed: {e}")
    if not out.get("ok"):
        raise SystemExit(f"upload failed: {out.get('error')} (is the Apps Script redeployed with the newest code?)")
    return out


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    secret = SYNC_SECRET_FILE.read_text().strip()
    villages = [slim(v) for v in load_villages(sys.argv[1])]
    batch, size, first, sent = {}, 0, True, 0
    for v in villages:
        s = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        if batch and size + len(s) > MAX_REQUEST_BYTES:
            post(secret, {"villages": batch, "reset": first}); first = False; sent += len(batch); batch, size = {}, 0
            print(f"  sent {sent}/{len(villages)}")
        batch[v["id"]] = s; size += len(s)
    if batch:
        post(secret, {"villages": batch, "reset": first}); sent += len(batch)
    print(f"Uploaded {sent} villages to the Soi tab.")


if __name__ == "__main__":
    main()
