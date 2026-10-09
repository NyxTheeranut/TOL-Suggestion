# TOL Suggestion

Village &amp; building sales planner for BMA-West — search/demographic lookup,
a tag-based recommendation list, and a visit calendar — PBH / CM / ADMIN build the plans (a whole month for themselves, and today + tomorrow for each DRS from a weekday roster); a DRS only sees what was assigned to them — in which every planned place is reserved for 7 days — read
live from a Google Sheet, same architecture as `TOL Tracker` / `L2 Discount
Map` / `Route Planner` elsewhere in this Dashboard.

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
   │   returns Villages/Buildings/CalendarTheme/CalendarPlan  "CalendarTheme" / "CalendarPlan" /
   ▼   (today + tomorrow only) + who else holds which place   "CalendarClaims" tabs
this page renders Search / Recommend / Calendar from that payload
```

`sync/aggregate_suggestion.py` curates the two raw BMA-West inventory exports
(`data/BMA-West - Village Inventory_2026.xlsb`, `data/BMA-West TOL - Building
Inventory_2026.xlsx` — **not committed to this repo**, see `.gitignore`) into
compact per-property records with tags (`high_available`, `large`,
`high_competitor`, `low_fault`) and a composite `autoScore`.
`sync/update_suggestion_sheet.py` pushes those into the Sheet's `Villages` /
`Buildings` tabs; it never touches `CalendarTheme` / `CalendarPlan` — those
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

### 4. Add your team to the Users tab

Open the page and sign in once — this auto-creates a "Users" tab with a
sample row (until then, everyone gets "not set up yet"). Edit that row (or
add rows) for your team: `email | note`. Delete the sample row.

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

### 6. Deploy to GitHub Pages

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
- `Users` — `email | role | note` allow-list. `role` is `PBH`, `CM`, `ADMIN` or `DRS`
  (blank/unknown = DRS): PBH / CM / ADMIN get the monthly calendar (switchable to a two-day view — it is one plan), the DRS roster, and the tools to assign every
  DRS's two days; a DRS gets a read-only view of what was assigned to them.
- `CalendarTheme` — one row per (month, viewer): that viewer's selected
  theme tags (`updatedBy` is the key, not just `monthKey`).
- `CalendarPlan` — one row per (month, day, slot, viewer) pick. Everyone saves their two days via
  `saveCalendarDays` — only **today and tomorrow** (Asia/Bangkok) can be written and
  `myData` only returns those two days. PBH / CM save a whole month via
  `saveCalendarMonth`. `myData` returns only the signed-in viewer's own rows (managers
  additionally get every DRS's two-day rows, read-only).
- `DrsRoster` — `weekday | email | updatedBy | updatedAt`: which DRS work on which weekday (1 = Mon … 7 = Sun), set by PBH / CM / ADMIN.
- `CalendarSlots` — up to 3 saved month plans per PBH / CM.
- `CalendarClaims` — `kind | refId | email | claimedAt | expiresAt`.
  Planning a place reserves it for 7 days (counted from `claimedAt`); nobody else
  can plan it until it expires or its owner removes it — then its row is deleted
  (`releasedAt` is a legacy column, always empty). Saves take a
  script lock, so two people racing for one place can't both get it; losers are
  told which picks to replace. A DRS can be given at most 20 new places per day,
  a PBH / CM 500 (`CALENDAR_CLAIM_DAILY_CAP[_MANAGER]`). The role is checked
  server-side on every save (`wrong_role` otherwise).

## Security notes

Same as the other projects in this Dashboard: the Apps Script deployment
uses "Anyone" access, but the real gate is the Google ID token (verified
directly against Google, checked against this app's own Client ID) plus the
Users tab. `syncData` can't go through that check at all (it's a script, not
a person signing in), so it's gated by `SYNC_SECRET` instead. The raw
`data/*.xlsb` / `*.xlsx` exports are never committed — see `.gitignore`.
