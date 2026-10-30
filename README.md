# TOL Suggestion

Village &amp; building sales planner for BMA-West — a map with search, a
tag-based recommendation list, and the **DRS dispatch board**: PBH / CM / ADMIN
plan every team's next 14 days (who works, which villages, and for a big
village which streets each person walks) and each DRS opens **แผนของฉัน** to see
only their own plan for today and tomorrow. Every planned place is reserved for
7 days. Everything is read live from a Google Sheet, same architecture as
`TOL Tracker` / `L2 Discount Map` / `Route Planner` elsewhere in this Dashboard.

Anyone signed in with an allow-listed Google account sees the same full
dataset — the page itself ships with no data at all; it's fetched after
sign-in.

## How it fits together

```
Browser (this page, hosted on GitHub Pages)
   │  Google Sign-In (Google Identity Services)
   ▼
Apps Script Web App  ──executes as the Sheet owner──▶  Google Sheet
   │   verifies the ID token against Google directly        "Users" tab (who's allowed in)
   │   checks the signed-in email is in the "Users" tab      "Villages" / "Buildings" tabs
   │   returns Villages/Buildings + the plans, roster,      "CalendarPlan" / "DrsRoster" /
   ▼   notes and street splits that viewer may see          "PlanNotes" / "PlanSplits" / "Soi" /
this page renders Map / Analysis / the dispatch   "CalendarClaims" tabs
board / แผนของฉัน from that payload
```

`sync/aggregate_suggestion.py` curates the two raw BMA-West inventory exports
(`data/BMA-West - Village Inventory_2026.xlsb`, `data/BMA-West TOL - Building
Inventory_2026.xlsx` — **not committed to this repo**, see `.gitignore`) into
compact per-property records with tags (`high_available`, `large`,
`high_competitor`, `low_fault`) and a composite `autoScore`.
`sync/update_suggestion_sheet.py` pushes those into the Sheet's `Villages` /
`Buildings` tabs; it never touches `CalendarPlan` / `DrsRoster` / `PlanNotes` / `PlanSplits` — those
belong to the live page itself (any signed-in viewer plans today's and
tomorrow's picks (DRS) or a month's (PBH / CM); each place planned is reserved
for that viewer for 7 days).

Run `python3 sync/aggregate_suggestion.py --sample` any time to eyeball the
curated fields + tag counts against the raw workbooks before trusting a sync.

## Repo layout

```
index.html        the whole site -- GitHub Pages serves this from repo root
manifest.json      Android/iOS "Add to Home Screen" metadata
icons/             app icons (source + generated apple-touch/192/512 sizes)
assets/            boundaries.js -- district/subdistrict map polygons
backend/           Sheets Sync - Apps Script Code.gs (paste into Apps Script)
sync/              the local data pipeline (aggregate_suggestion.py,
                   sheet_schema.py, update_suggestion_sheet.py)
data/              raw source workbooks -- gitignored, never committed
```

## One-time setup

### 1. Google Cloud Console — OAuth Client ID

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and
   create a new project dedicated to this app — keep it separate from the
   other Dashboard projects' OAuth clients.
2. **APIs & Services → OAuth consent screen** — configure it, add yourself as
   a test user if it stays in "Testing" publish status.
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
   - Application type: **Web application**
   - Authorized JavaScript origins:
     - `https://<your-github-username>.github.io`
     - `http://localhost:8094` (for local testing via
       `Start TOL Suggestion.command`)
4. Copy the **Client ID**.

### 2. Google Sheet + Apps Script backend

1. Create a new Google Sheet, dedicated to this app.
2. **Extensions → Apps Script**, delete the starter code, paste in the full
   contents of `backend/Sheets Sync - Apps Script Code.gs` from this repo.
3. **Project Settings → Script Properties** → add:
   - `OAUTH_CLIENT_ID` = the Client ID from step 1.
   - `SYNC_SECRET` = any random string (e.g. `openssl rand -hex 24`).
     Gates `syncData`, used only by `sync/update_suggestion_sheet.py`.
4. **Deploy → New deployment** — Type: **Web app**, Execute as: **Me**,
   Who has access: **Anyone** (real access control is the ID-token + Users
   tab check inside the script, not this setting).
5. Deploy, authorize when prompted, copy the **Web app URL**.

### 3. Wire the two together

1. In `index.html`, set `GOOGLE_CLIENT_ID` and `DEFAULT_SYNC_URL` (near the
   bottom of the `<script>` block) to the values from steps 1–2. Until these
   are set, the page auto-loads a small embedded mock dataset instead of
   requiring sign-in, so the UI can be reviewed locally first.
2. In `sync/update_suggestion_sheet.py`, set `SYNC_URL` to the same Web app URL.

### 4. Add your people to the Users tab

Open the page and sign in once — this auto-creates a "Users" tab with a
sample row (until then, everyone gets "not set up yet"). Fill one row per person:

| column | meaning |
|---|---|
| `email` | the Google account they sign in with |
| `role` | `PBH`, `CM`, `ADMIN` or `DRS` (blank = DRS) |
| `name` | shown on the board and in แผนของฉัน |
| `team` | DRS only — the team they work in (a DRS without a team is a team of one) |
| `owner` | DRS only — the **email of the CM** who runs them. A CM sees and edits only the DRS whose `owner` is them; PBH and ADMIN see everyone. A DRS with a blank `owner` is visible to PBH / ADMIN only. If the column does not exist at all, every manager sees every DRS (the old behaviour). |
| `leader` | DRS only — `TRUE` for the team leader. You normally do not type this: click "ตั้งเป็น Leader" on the board (the page writes this column) |

Real names and emails live **only** in this tab, never in the page or the repo.

### 5. Push the data

Create a file containing exactly the `SYNC_SECRET` value from step 2 (no
extra whitespace) outside this repo, in the Dashboard folder's `Config/`
directory — `sync/update_suggestion_sheet.py` reads `Config/sync_secret.txt`
(shared with this Dashboard's other projects rather than a dedicated file;
point it at your own `SYNC_SECRET` if you'd rather keep it separate).
**Never commit this file** — it lives outside the repo specifically so it
can't be.

Then run:

```
python3 sync/update_suggestion_sheet.py
```

(or double-click `Update TOL Suggestion.command` in the Dashboard folder's
`Launchers/`) to populate the `Villages` / `Buildings` tabs. Re-run any time
a fresh export lands in `data/`.

### 6. Street data for splitting big villages (optional)

For a big village the board can split its sois (streets) between the people
working that day and show each person their own path on a map. The street
data is **not** in this repo (the repo behind GitHub Pages is public): put the
file the soi pipeline produced next to the page as `assets/soi-data.js` (it is
git-ignored) and upload it to the Sheet once:

```
python3 sync/upload_soi.py assets/soi-data.js
```

It fills the `Soi` tab; the page reads it only for signed-in, allow-listed users
(`getSoi`). Without it everything else works; the "แบ่งซอย" tab is simply absent.

### 7. Deploy to GitHub Pages

Push this repo to GitHub, then **Settings → Pages → Source: Deploy from a
branch → `main` / `(root)`**.

## Local testing

`Start TOL Suggestion.command` (in the Dashboard folder's `Launchers/`)
serves this page over `http://localhost:8094` — needed for real Google
Sign-In (`file://` isn't an allowed origin). Before the Google Cloud/Sheet
setup above is done, the page works anyway with its embedded mock dataset —
useful for reviewing the UI without any of that setup.

## What's in each tab

- `Meta` — sync timestamp, source file names, row counts.
- `Villages` / `Buildings` — one row per property: location, household/unit
  counts, ports, latest active, competitor total, market share, fault/churn,
  the business's own existing sales grading, computed tags, and `autoScore`.
- `Users` — `email | role | name | team | owner | leader` allow-list (see step 4). PBH / CM / ADMIN get the
  dispatch board; a DRS gets only **แผนของฉัน** (no map, no analysis, no board): the server sends a DRS just the villages / buildings in their own two-day plan, and street (soi) data only for those villages. The server filters everything by `owner`: a CM can read and write
  only their own DRS's plans, roster, notes and splits (`wrong_role` / `not_a_drs` / `wrong_team` otherwise).
- `CalendarTheme` — legacy (the monthly calendar was removed); no longer written.
- `CalendarPlan` — one row per (month, day, slot, DRS) pick, written by managers with `assignDrsDays` (up to 14 days ahead).
  `myData` gives a DRS only their own rows for **today and tomorrow** (Asia/Bangkok). Slots `1`–`3` are the main places, `backup` the spare.
- `DrsRoster` — which DRS work when: weekly pattern rows (`weekday` 1 = Mon … 7 = Sun) and one-date changes
  (`weekday` 0, `date` YYYYMMDD, `mode` add/remove). "Who works that day" on the board is read from here (there is no leave concept).
- `PlanNotes` — `monthKey | day | team | note | updatedBy | updatedAt`: the message from the lead to a team for one day. Shown to that team's DRS.
- `PlanSplits` — `monthKey | day | team | villageId | json | …`: the saved street split of one village for one team-day
  (`{crew:[emails], own:[person index per street], basis}`). A DRS gets the rows of their own team for today and tomorrow.
- `Soi` — `villageId | chunkIndex | chunkCount | chunk`: street geometry of the big villages (JSON cut into ≤ 45,000-character
  pieces), uploaded by `sync/upload_soi.py`, served only after sign-in.
- `CalendarSlots` — legacy (saved month plans); no longer used.
- `CalendarClaims` — `kind | refId | email | claimedAt | expiresAt`.
  Planning a place reserves it for at least 7 days (from `claimedAt`) and until its planned day is over, whichever is later; nobody else
  can plan it until it expires or its owner removes it — then its row is deleted
  (`releasedAt` is a legacy column, always empty). Saves take a
  script lock, so two people racing for one place can't both get it; losers are
  told which picks to replace. A DRS can be given at most 20 new places per day,
  a PBH / CM / ADMIN 1,500 (`CALENDAR_CLAIM_DAILY_CAP[_MANAGER]`); managers can assign a DRS up to 14 days ahead (`CALENDAR_ASSIGN_DAYS`), a DRS still receives only today + tomorrow. The role is checked
  server-side on every save (`wrong_role` otherwise).

## Security notes

Same as the other projects in this Dashboard: the Apps Script deployment
uses "Anyone" access, but the real gate is the Google ID token (verified
directly against Google, checked against this app's own Client ID) plus the
Users tab. `syncData` can't go through that check at all (it's a script, not
a person signing in), so it's gated by `SYNC_SECRET` instead. The raw
`data/*.xlsb` / `*.xlsx` exports are never committed — see `.gitignore`.
