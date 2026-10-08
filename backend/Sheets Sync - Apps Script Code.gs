/**
 * TOL Suggestion -- Google Sheets backend (sign-in gate + live data).
 *
 * What this is: the API the hosted TOL Suggestion page calls. It runs inside
 * a Google Sheet, as that Sheet's owner -- same "no server to host or pay
 * for, the Sheet itself is the database" model as TOL Tracker / L2 Discount
 * Map / Route Planner, and the same "every allow-listed viewer sees the same
 * full dataset" (no per-person scoping).
 *
 * ── Data layout ────────────────────────────────────────────────────────────
 * aggregate_suggestion.py curates the two BMA-West master inventory exports
 * (villages, buildings) into compact per-property records; sheet_schema.py's
 * flatten() decomposes those into the Meta/Villages/Buildings tabs below
 * (Python, single source of truth for that shape) -- reconstructPayload_
 * here is a hand-ported mirror of sheet_schema.reconstruct().
 *   Meta        1 row of sync metadata (source file names, row counts, when).
 *   Villages    1 row per village -- location, household/port counts, latest
 *               active, competitor total, market share, fault/churn grades,
 *               the business's own existing sales grading, tags, autoScore.
 *   Buildings   1 row per building -- same idea, MDU-shaped fields (floors,
 *               units, occupancy, ARPU) instead of household count.
 * Unlike those two, CalendarTheme and CalendarPlan are NOT written by the
 * local Python sync -- they belong to the live page itself:
 *   CalendarTheme  1 row per month -- monthKey | tagsCsv | updatedBy | updatedAt.
 *   CalendarPlan   1 row per (month, day, slot) pick -- monthKey | day | slot
 *                  ("1"/"2"/"3"/"backup") | kind ("village"/"building") | refId.
 *                  Only today + tomorrow (Asia/Bangkok) can be saved or are
 *                  sent to the page -- see saveCalendarDays_ below.
 *   CalendarClaims 1 row per place a viewer has planned -- kind | refId |
 *                  email | claimedAt | expiresAt | releasedAt. While active
 *                  (10 days from claimedAt, until released) nobody else can
 *                  plan that place.
 * "Users" -- who's allowed to view the dashboard: an email allow-list, no
 *   roles. Created automatically (with a sample row) the first time anyone
 *   signs in, same as every other project in this Dashboard.
 *
 * ── Auth ───────────────────────────────────────────────────────────────────
 * Google Identity Services on the page, verified against Google directly
 * here (audience checked against OAUTH_CLIENT_ID) -- see TOL Tracker's
 * Apps Script for the fuller writeup, reused near-verbatim below.
 *   myData                          -> requires the email to be a Users row.
 *   getSyncData / syncData          -> update_suggestion_sheet.py, on your
 *                                      own machine -- gated by SYNC_SECRET
 *                                      instead (not a person signing in).
 *   saveCalendarTheme / saveCalendarDays -> any allow-
 *                                      listed signed-in viewer (no separate admin role -- matches
 *                                      this dashboard's no-roles model).
 *
 * ── SETUP (one-time) -- see this repo's README.md for the full walkthrough ─
 * Same shape as TOL Tracker's: OAuth Client ID + a dedicated Sheet running
 * this script, both Script Properties (OAUTH_CLIENT_ID, SYNC_SECRET) set,
 * deployed as a Web App (Execute as: Me, Who has access: Anyone -- real
 * access control is the ID-token + Users-tab check, not this setting).
 */

var TAB_NAMES = ["Meta", "Villages", "Buildings", "CalendarTheme", "CalendarPlan"];

var VILLAGE_FIELDS = [
  "id", "name", "district", "subdistrict", "scab", "hopHoz", "lat", "lng",
  "status", "closed", "houseAll", "network", "totalPort", "totalAvailable",
  "fttbPort", "fttbAvailable", "fttcPort", "fttcAvailable", "allPort", "allAvailable", "active",
  "activePrevMonth",
  "activePct", "competitor", "mksTrue", "mksFibre3", "mksNt", "competitorMks",
  "competitorSubs", "trueAvgDl", "trueMaxDl", "fibre3AvgDl", "fibre3MaxDl",
  "faultAvg", "faultGrade", "faultTruckRoll",
  "faultTruckRollPct", "faultOther", "faultOtherPct", "churn3m",
  "churnRate", "churnGrade", "churnVolMonthly", "churnInvolMonthly",
  "churnMonthly", "churnMonthlyRate", "gradeSale",
  "scoreSale", "gradeCare", "scoreCare", "finalGrade", "villageGrade",
  "actionGroup", "mainGroup", "contractEnd", "tags", "autoScore",
  "hasL2", "l2Points", "l2Mkts",
];

var BUILDING_FIELDS = [
  "id", "name", "prov", "amp", "tam", "scab", "lat", "lng", "closed",
  "groupType", "subGroupType", "mduModel", "developer", "floors", "units",
  "occupancy", "network", "totalPort", "totalAvailable",
  "fttbPort", "fttbAvailable", "fttcPort", "fttcAvailable", "allPort", "allAvailable", "active", "activePrevMonth",
  "activePct",
  "arpu", "competitor", "mksTrue", "mksFibre3", "mksNt", "competitorMks",
  "competitorSubs", "trueAvgDl", "trueMaxDl", "fibre3AvgDl", "fibre3MaxDl",
  "faultAvg", "faultGrade", "faultTruckRoll",
  "faultTruckRollPct", "faultOther", "faultOtherPct", "churn3m", "churnPct",
  "churnVolMonthly", "churnInvolMonthly", "churnMonthly", "churnMonthlyRate",
  "gradeSale", "scoreSale",
  "groupBuilding", "caretakerChannel", "caretakerName", "tags", "autoScore",
];

// Columns that must stay plain text -- Sheets otherwise auto-detects a
// numeric-looking string as a real number (mangling a long ID) or a
// human-readable string as a date. See writeTab_ below.
var TEXT_COLUMNS = [
  "id", "name", "district", "subdistrict", "scab", "hopHoz", "status", "network",
  "faultGrade", "churnGrade", "gradeSale", "gradeCare", "finalGrade",
  "villageGrade", "actionGroup", "mainGroup", "tags",
  "prov", "amp", "tam", "groupType", "subGroupType", "mduModel",
  "developer", "groupBuilding", "caretakerChannel", "caretakerName",
  "villageFile", "buildingFile", "syncedAt",
  "monthKey", "kind", "refId", "slot", "tagsCsv", "updatedBy", "updatedAt",
  "l2Points", "l2Mkts",
];

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);

    if (body.action === "myData") {
      return jsonResponse_(myData_(body.idToken));
    }

    if (body.action === "getSyncData") {
      // Read-only counterpart of syncData, for update_suggestion_sheet.py --
      // gated by the same secret as syncData (this was missing in an
      // earlier version of this file: without it, anyone with the
      // deployment URL could read the whole dataset with no secret and no
      // sign-in at all).
      requireSyncSecret_(body.secret);
      var tabs = readTabs_(["Meta", "Villages", "Buildings"]);
      return jsonResponse_({ ok: true, payload: tabs ? reconstructPayload_(tabs) : null });
    }

    if (body.action === "syncData") {
      // update_suggestion_sheet.py only, gated by SYNC_SECRET -- see the
      // header comment above and TOL Tracker's syncBbData for why.
      requireSyncSecret_(body.secret);
      var result = syncData_(body.tabs || {});
      return jsonResponse_({ ok: true, tabs: result.tabs, rows: result.rows });
    }

    if (body.action === "saveCalendarTheme") {
      var email = verifyIdToken_(body.idToken);
      if (!email || !isAllowedUser_(email)) return jsonResponse_({ ok: false, error: "not_signed_in" });
      saveCalendarTheme_(body.monthKey, body.tags || [], email);
      return jsonResponse_({ ok: true });
    }

    // The calendar only ever covers today and tomorrow (Asia/Bangkok), and
    // every place a viewer plans is "claimed" for CALENDAR_CLAIM_DAYS so no
    // other viewer is handed it -- see saveCalendarDays_. The old whole-month
    // / saved-slot actions are gone on purpose: they would let one viewer
    // hold a month of the company's village list at a time.
    if (body.action === "saveCalendarDays") {
      var email2 = verifyIdToken_(body.idToken);
      if (!email2 || !isAllowedUser_(email2)) return jsonResponse_({ ok: false, error: "not_signed_in" });
      return jsonResponse_(saveCalendarDays_(body.days || [], email2));
    }

    return jsonResponse_({ ok: false, error: "unknown action" });
  } catch (err) {
    return jsonResponse_({ ok: false, error: String(err) });
  }
}

function doGet(e) {
  return jsonResponse_({ ok: true });
}

// ---------- auth (same shape as TOL Tracker's) ----------

function requireSyncSecret_(secret) {
  var expected = PropertiesService.getScriptProperties().getProperty("SYNC_SECRET");
  if (!expected) throw new Error("SYNC_SECRET script property is not set -- see setup notes at the top of this file");
  if (secret !== expected) throw new Error("forbidden: bad sync secret");
}

function verifyIdToken_(idToken) {
  if (!idToken) return null;
  var resp = UrlFetchApp.fetch(
    "https://oauth2.googleapis.com/tokeninfo?id_token=" + encodeURIComponent(idToken),
    { muteHttpExceptions: true },
  );
  if (resp.getResponseCode() !== 200) return null;
  var data = JSON.parse(resp.getContentText());
  var expectedClientId = PropertiesService.getScriptProperties().getProperty("OAUTH_CLIENT_ID");
  if (!expectedClientId) throw new Error("OAUTH_CLIENT_ID script property is not set -- see setup notes at the top of this file");
  if (data.aud !== expectedClientId) return null;
  if (!data.email || data.email_verified !== "true") return null;
  return data.email;
}

function isAllowedUser_(email) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("Users");
  if (!sheet) {
    sheet = ss.insertSheet("Users");
    sheet.appendRow(["email", "note"]);
    sheet.appendRow(["example@gmail.com", "sample row -- replace with your team, then delete this"]);
    sheet.setFrozenRows(1);
    return false;
  }
  var data = sheet.getDataRange().getValues();
  var header = data[0];
  var emailCol = header.indexOf("email");
  if (emailCol === -1) return false;
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][emailCol]).trim().toLowerCase() === email.toLowerCase()) return true;
  }
  return false;
}

// ---------- myData ----------

function myData_(idToken) {
  var email = verifyIdToken_(idToken);
  if (!email) return { ok: false, error: "not_signed_in" };
  if (!isAllowedUser_(email)) {
    return {
      ok: false, error: "no_access",
      message: "This Google account (" + email + ") isn't set up yet. Ask an admin to add it to the Users tab.",
    };
  }
  var tabs = readTabs_(["Meta", "Villages", "Buildings", "CalendarTheme", "CalendarPlan"]);
  if (!tabs) return { ok: false, error: "no_data", message: "No data has been synced yet -- run update_suggestion_sheet.py." };
  // Each signed-in viewer only ever sees their OWN calendar plan/theme --
  // see the header comment above CALENDAR_PLAN_HEADER for why this exists
  // (two people planning the same month were overwriting each other).
  tabs.CalendarPlan = windowPlanRows_(filterOwnRows_(tabs.CalendarPlan, "email", email)); // only today + tomorrow leave the server
  tabs.CalendarTheme = filterOwnRows_(tabs.CalendarTheme, "updatedBy", email);
  // Sends the raw tabs, NOT reconstructPayload_(tabs) -- rebuilding the full
  // nested shape (~1,500 property rows into tagged/typed objects) is real
  // CPU work, and doing it here means every sign-in pays for it inside
  // Apps Script's slower, quota-metered runtime before the viewer sees
  // anything at all (this was almost certainly why sign-in was timing out).
  // The browser does the identical reconstruction (reconstructPayload_
  // ported verbatim into index.html) in its own fast JS engine instead --
  // same "ship raw, reconstruct client-side" split TOL Tracker's myBbData_
  // already uses, and for the same reason.
  return { ok: true, email: email, tabs: tabs, claimedByOthers: claimedByOthers_(email) };
}

// Passes a tab through unfiltered if it doesn't have the given column yet
// (an old Sheet from before per-user calendars existed) rather than
// erroring or hiding everything -- graceful migration, not a hard cutover.
function filterOwnRows_(tab, colName, email) {
  var idx = tab.header.indexOf(colName);
  if (idx === -1) return tab;
  return { header: tab.header, rows: tab.rows.filter(function (row) { return String(row[idx]) === email; }) };
}

function readTabs_(names) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tabs = {};
  for (var i = 0; i < names.length; i++) {
    var name = names[i];
    var sheet = ss.getSheetByName(name);
    if (!sheet) {
      if (name === "CalendarTheme" || name === "CalendarPlan") {
        tabs[name] = { header: [], rows: [] }; // not created yet -- empty is fine
        continue;
      }
      return null; // no sync has ever run
    }
    var values = sheet.getDataRange().getValues();
    tabs[name] = values.length ? { header: values[0], rows: values.slice(1) } : { header: [], rows: [] };
  }
  return tabs;
}

// ---------- syncData: update_suggestion_sheet.py's write path ----------

function syncData_(tabs) {
  var names = Object.keys(tabs);
  var totalRows = 0;
  for (var i = 0; i < names.length; i++) {
    var name = names[i];
    if (TAB_NAMES.indexOf(name) === -1) throw new Error("unknown tab in payload: " + name);
    var t = tabs[name];
    writeTab_(name, t.header, t.rows, !!t.append);
    totalRows += t.rows.length;
  }
  return { tabs: names.length, rows: totalRows };
}

function writeTab_(name, header, rows, append) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(name);
  if (!sheet) sheet = ss.insertSheet(name);

  if (append) {
    if (!rows.length) return;
    var startRow = sheet.getLastRow() + 1;
    header.forEach(function (colName, idx) {
      var fmt = TEXT_COLUMNS.indexOf(colName) !== -1 ? "@" : "0.####";
      sheet.getRange(startRow, idx + 1, rows.length, 1).setNumberFormat(fmt);
    });
    sheet.getRange(startRow, 1, rows.length, header.length).setValues(rows);
    return;
  }

  sheet.clearContents();
  sheet.clearFormats(); // see TOL Tracker's writeTab_ for why this matters
  var allRows = [header].concat(rows);
  if (!allRows.length || !header.length) {
    sheet.setFrozenRows(1);
    return;
  }
  header.forEach(function (colName, idx) {
    var fmt = TEXT_COLUMNS.indexOf(colName) !== -1 ? "@" : "0.####";
    sheet.getRange(1, idx + 1, allRows.length, 1).setNumberFormat(fmt);
  });
  sheet.getRange(1, 1, allRows.length, header.length).setValues(allRows);
  sheet.setFrozenRows(1);
  sheet.autoResizeColumns(1, header.length);
}

// ---------- CalendarTheme / CalendarPlan: owned by the live page ----------
// Both are small (a handful of rows per month), so a targeted read-modify-
// write here is simple and cheap -- unlike Villages/Buildings, these are
// NEVER wiped wholesale; only rows for the affected monthKey (and day, for
// CalendarPlan) are replaced. Two things keep these from just accumulating
// forever, month after month: pruneOldCalendarData_ (called from every
// write below) drops anything older than CALENDAR_RETENTION_MONTHS, and
// clearCalendarMonth_ lets a viewer wipe one month on demand from the page.
//
// Every row also carries WHO planned it (CalendarPlan.email, CalendarTheme's
// existing "updatedBy") -- each signed-in viewer only ever reads and writes
// their OWN rows (see myData_'s filterOwnRows_ and the email-scoped
// read-modify-write below), so two people planning the same month never
// overwrite each other's picks. Not a shared team plan; a personal one per
// Google account.

// "name" (CalendarPlan) is a snapshot of the village/building's name at the
// time it was picked, so a plan still reads properly if the property later
// disappears from Villages/Buildings. Old rows from before that column
// existed are padded with "" by normalizeRows_ on the next write.
var CALENDAR_THEME_HEADER = ["monthKey", "tagsCsv", "updatedBy", "updatedAt"];
var CALENDAR_PLAN_HEADER = ["monthKey", "day", "slot", "kind", "refId", "email", "name"];
var CALENDAR_RETENTION_MONTHS = 2; // keep the current month plus this many months back

function cutoffMonthKey_() {
  var d = new Date();
  d.setDate(1); // pin to the 1st first, so subtracting months can't skid into the wrong month on a 31st
  d.setMonth(d.getMonth() - CALENDAR_RETENTION_MONTHS);
  var m = d.getMonth() + 1;
  return String(d.getFullYear()) + (m < 10 ? "0" + m : String(m));
}

// Forces every row to exactly `width` columns (padding short ones,
// trimming long ones) and drops rows with no key in column 0 at all --
// a blank row, or Sheets' own [[""]] minimum on an otherwise-empty
// range. Without this, a row shaped for an OLDER version of this schema
// (e.g. from before the "email" column existed) sitting next to a
// freshly-built row gets handed to setValues() as a jagged array, which
// throws instead of writing anything ("data has N columns but range has
// M columns" -- exactly what happened here). Every read-modify-write
// below runs its kept rows through this before writing, so the sheet
// self-heals back to the current column shape on the next successful
// write instead of staying corrupted.
// CalendarPlan columns that hold ids/names must stay TEXT. Without this
// Sheets turns a numeric-looking refId ("120507010004") into a number on
// write, and the page then fails to match it to its village.
function textifyPlanRows_(rows) {
  return rows.map(function (r) {
    r[3] = String(r[3]); r[4] = String(r[4]); r[6] = r[6] == null ? "" : String(r[6]);
    return r;
  });
}
function formatPlanTextColumns_(sheet) {
  // kind, refId, email, name -- set before writing so strings stay strings.
  [4, 5, 6, 7].forEach(function (col) {
    sheet.getRange(1, col, sheet.getMaxRows(), 1).setNumberFormat("@");
  });
}

function normalizeRows_(rows, width) {
  return rows
    .filter(function (r) { return r && r[0] !== "" && r[0] != null; })
    .map(function (r) {
      r = r.slice(0, width);
      while (r.length < width) r.push("");
      return r;
    });
}

// Drops CalendarPlan/CalendarTheme rows for any month older than the
// retention window -- a past month's sales-visit plan has no ongoing
// value once it's over, unlike Villages/Buildings which are a current-
// state snapshot worth keeping in full. Runs on every calendar write
// (small, cheap tabs) rather than as a separate scheduled job, so there's
// nothing extra to set up or forget to run.
function pruneOldCalendarData_() {
  var cutoff = cutoffMonthKey_();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  [["CalendarPlan", CALENDAR_PLAN_HEADER], ["CalendarTheme", CALENDAR_THEME_HEADER]].forEach(function (pair) {
    var sheet = ss.getSheetByName(pair[0]);
    if (!sheet) return;
    var header = pair[1];
    var data = sheet.getDataRange().getValues();
    if (data.length < 2) return;
    var kept = normalizeRows_(data.slice(1).filter(function (r) { return String(r[0]) >= cutoff; }), header.length);
    if (kept.length === data.length - 1) return; // nothing to prune -- skip the rewrite
    sheet.clearContents();
    if (pair[0] === "CalendarPlan") { formatPlanTextColumns_(sheet); textifyPlanRows_(kept); }
    sheet.getRange(1, 1, 1, header.length).setValues([header]);
    if (kept.length) sheet.getRange(2, 1, kept.length, header.length).setValues(kept);
    sheet.setFrozenRows(1);
  });
}

function saveCalendarTheme_(monthKey, tags, email) {
  if (!monthKey) throw new Error("monthKey required");
  pruneOldCalendarData_();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("CalendarTheme");
  if (!sheet) {
    sheet = ss.insertSheet("CalendarTheme");
    sheet.appendRow(CALENDAR_THEME_HEADER);
    sheet.setFrozenRows(1);
  }
  var data = sheet.getDataRange().getValues();
  var existing = normalizeRows_(data.slice(1), CALENDAR_THEME_HEADER.length);
  var rowIdx = -1;
  for (var i = 0; i < existing.length; i++) {
    // Matched on monthKey + updatedBy (not monthKey alone) -- each viewer
    // gets their own theme row per month.
    if (String(existing[i][0]) === String(monthKey) && existing[i][2] === email) { rowIdx = i; break; }
  }
  var newRow = [monthKey, tags.join(","), email, new Date().toISOString()];
  if (rowIdx === -1) existing.push(newRow); else existing[rowIdx] = newRow;
  sheet.clearContents();
  sheet.getRange(1, 1, 1, CALENDAR_THEME_HEADER.length).setValues([CALENDAR_THEME_HEADER]);
  if (existing.length) sheet.getRange(2, 1, existing.length, CALENDAR_THEME_HEADER.length).setValues(existing);
  sheet.setFrozenRows(1);
}

// ---------- Calendar window + place claims ----------
//
// Two rules keep the company's village list from leaking out of the calendar
// and keep reps from being sent to the same place:
//   1. The calendar only covers TODAY and TOMORROW (Asia/Bangkok). Earlier
//      days drop off on their own, and myData_ only returns those two days,
//      so a rep never holds more than two days of picks at a time.
//   2. Planning a place CLAIMS it for CALENDAR_CLAIM_DAYS, counted from the
//      moment it was first planned. While a claim is active nobody else can
//      plan that place (the page leaves it out of auto-picking, and this
//      function refuses it if two reps race for it). Removing or replacing a
//      pick releases the claim straight away; otherwise it simply expires.
// CALENDAR_CLAIM_DAILY_CAP stops someone from regenerating over and over to
// read through the whole pool (every new place a rep is handed counts).

var CALENDAR_CLAIM_HEADER = ["kind", "refId", "email", "claimedAt", "expiresAt", "releasedAt"];
var CALENDAR_CLAIM_DAYS = 10;
var CALENDAR_CLAIM_DAILY_CAP = 20;
var CALENDAR_WINDOW_DAYS = 2;
var CALENDAR_TZ = "Asia/Bangkok";

function windowDays_() {
  var out = [], now = Date.now();
  for (var i = 0; i < CALENDAR_WINDOW_DAYS; i++) {
    var ymd = Utilities.formatDate(new Date(now + i * 86400000), CALENDAR_TZ, "yyyyMMdd");
    out.push({ key: ymd, monthKey: ymd.slice(0, 6), day: Number(ymd.slice(6, 8)) });
  }
  return out;
}

function windowAllowed_() {
  var allowed = {};
  windowDays_().forEach(function (w) { allowed[w.monthKey + ":" + w.day] = true; });
  return allowed;
}

// Keeps only plan rows that fall on today or tomorrow.
function windowPlanRows_(tab) {
  if (!tab.header || !tab.header.length) return tab;
  var allowed = windowAllowed_();
  return { header: tab.header, rows: tab.rows.filter(function (row) { return allowed[String(row[0]) + ":" + Number(row[1])]; }) };
}

function claimsSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("CalendarClaims");
  if (!sheet) {
    sheet = ss.insertSheet("CalendarClaims");
    sheet.getRange(1, 1, sheet.getMaxRows(), CALENDAR_CLAIM_HEADER.length).setNumberFormat("@"); // ids and timestamps stay text
    sheet.getRange(1, 1, 1, CALENDAR_CLAIM_HEADER.length).setValues([CALENDAR_CLAIM_HEADER]);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function readClaims_() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("CalendarClaims");
  if (!sheet) return [];
  return normalizeRows_(sheet.getDataRange().getValues().slice(1), CALENDAR_CLAIM_HEADER.length).map(function (r) {
    return r.map(function (v) { return v == null ? "" : String(v); });
  });
}

function claimIsActive_(row, nowMs) {
  return !row[5] && new Date(row[4]).getTime() > nowMs;
}

// Active claims held by SOMEONE ELSE, as [kind, refId] pairs only -- enough
// for the page to skip those places, with no names or emails.
function claimedByOthers_(email) {
  var nowMs = Date.now();
  return readClaims_().filter(function (r) { return claimIsActive_(r, nowMs) && r[2] !== email; })
    .map(function (r) { return [r[0], r[1]]; });
}

// days: [{monthKey, day, picks: [{slot, kind, refId, name}]}] -- replaces the
// caller's picks for each given day (an empty picks list clears the day).
// Returns {ok, conflicts: [{kind, refId}], capReached, claimedByOthers}.
// Conflicts are picks that someone else holds (or that would exceed today's
// cap); they are NOT saved, and the page picks replacements.
function saveCalendarDays_(days, email) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var allowed = windowAllowed_();
    var nowMs = Date.now(), nowIso = new Date(nowMs).toISOString();
    var todayKey = windowDays_()[0].key;
    var claims = readClaims_();
    var byKey = {};
    claims.forEach(function (r) { if (claimIsActive_(r, nowMs)) byKey[r[0] + ":" + r[1]] = r; });
    var newToday = claims.filter(function (r) {
      return r[2] === email && Utilities.formatDate(new Date(r[3]), CALENDAR_TZ, "yyyyMMdd") === todayKey;
    }).length;

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var planSheet = ss.getSheetByName("CalendarPlan");
    if (!planSheet) {
      planSheet = ss.insertSheet("CalendarPlan");
      planSheet.appendRow(CALENDAR_PLAN_HEADER);
      planSheet.setFrozenRows(1);
    }
    var width = CALENDAR_PLAN_HEADER.length;
    var kept = normalizeRows_(planSheet.getDataRange().getValues().slice(1), width);

    var conflicts = [], capReached = false, previous = {}, outside = 0;
    days.forEach(function (d) {
      var mk = String(d.monthKey), dn = Number(d.day);
      if (!allowed[mk + ":" + dn]) { outside++; return; }
      kept = kept.filter(function (row) {
        var mine = String(row[0]) === mk && Number(row[1]) === dn && row[5] === email;
        if (mine) previous[String(row[3]) + ":" + String(row[4])] = true;
        return !mine;
      });
      (d.picks || []).slice(0, 4).forEach(function (p) {
        var key = String(p.kind) + ":" + String(p.refId);
        var held = byKey[key];
        if (held && held[2] !== email) { conflicts.push({ kind: String(p.kind), refId: String(p.refId) }); return; }
        if (!held) {
          if (newToday >= CALENDAR_CLAIM_DAILY_CAP) { capReached = true; conflicts.push({ kind: String(p.kind), refId: String(p.refId) }); return; }
          var row = [String(p.kind), String(p.refId), email, nowIso, new Date(nowMs + CALENDAR_CLAIM_DAYS * 86400000).toISOString(), ""];
          claims.push(row); byKey[key] = row; newToday++;
        }
        kept.push([mk, dn, p.slot, p.kind, p.refId, email, p.name || ""]);
      });
    });

    // Release: places this caller had planned on these days but no longer
    // plans anywhere in the window go straight back to the pool.
    var stillPlanned = {};
    kept.forEach(function (row) {
      if (row[5] === email && allowed[String(row[0]) + ":" + Number(row[1])]) stillPlanned[String(row[3]) + ":" + String(row[4])] = true;
    });
    Object.keys(previous).forEach(function (key) {
      var c = byKey[key];
      if (!stillPlanned[key] && c && c[2] === email) { c[5] = nowIso; delete byKey[key]; }
    });

    planSheet.clearContents();
    formatPlanTextColumns_(planSheet);
    textifyPlanRows_(kept);
    planSheet.getRange(1, 1, 1, width).setValues([CALENDAR_PLAN_HEADER]);
    if (kept.length) planSheet.getRange(2, 1, kept.length, width).setValues(kept);
    planSheet.setFrozenRows(1);

    // Claim rows only need to live a little past their expiry (the daily cap
    // reads today's rows, released ones included).
    var cutoff = nowMs - (CALENDAR_CLAIM_DAYS + 2) * 86400000;
    claims = claims.filter(function (r) { return new Date(r[3]).getTime() >= cutoff; });
    var cs = claimsSheet_();
    cs.clearContents();
    cs.getRange(1, 1, cs.getMaxRows(), CALENDAR_CLAIM_HEADER.length).setNumberFormat("@");
    cs.getRange(1, 1, 1, CALENDAR_CLAIM_HEADER.length).setValues([CALENDAR_CLAIM_HEADER]);
    if (claims.length) cs.getRange(2, 1, claims.length, CALENDAR_CLAIM_HEADER.length).setValues(claims);
    cs.setFrozenRows(1);

    pruneOldCalendarData_();
    return { ok: true, conflicts: conflicts, capReached: capReached, outsideWindow: outside, claimedByOthers: claimedByOthers_(email) };
  } finally {
    lock.releaseLock();
  }
}

// ---------- reconstructPayload_: mirror of sheet_schema.reconstruct() ----------

function rowObjects_(tab, fields) {
  return tab.rows.map(function (row) {
    var obj = {};
    tab.header.forEach(function (h, i) { obj[h] = row[i]; });
    if (fields) {
      fields.forEach(function (f) { if (!(f in obj)) obj[f] = null; });
    }
    if ("tags" in obj) obj.tags = String(obj.tags || "").split(",").filter(function (t) { return t; });
    if ("closed" in obj) obj.closed = !!Number(obj.closed);
    if ("hasL2" in obj) obj.hasL2 = !!Number(obj.hasL2);
    if ("l2Points" in obj) {
      try { obj.l2Points = JSON.parse(obj.l2Points || "[]"); } catch (e) { obj.l2Points = []; }
    }
    if ("l2Mkts" in obj) {
      try { obj.l2Mkts = JSON.parse(obj.l2Mkts || "{}"); } catch (e) { obj.l2Mkts = {}; }
    }
    Object.keys(obj).forEach(function (k) { if (obj[k] === "") obj[k] = null; });
    return obj;
  });
}

function reconstructPayload_(tabs) {
  var meta = tabs.Meta.rows.length ? rowObjects_(tabs.Meta)[0] : {};
  var villages = rowObjects_(tabs.Villages, VILLAGE_FIELDS);
  var buildings = rowObjects_(tabs.Buildings, BUILDING_FIELDS);
  var calendarTheme = tabs.CalendarTheme ? rowObjects_(tabs.CalendarTheme) : [];
  var calendarPlan = tabs.CalendarPlan ? rowObjects_(tabs.CalendarPlan) : [];
  return {
    meta: meta,
    villages: villages,
    buildings: buildings,
    calendarTheme: calendarTheme,
    calendarPlan: calendarPlan,
  };
}

// ---------- shared ----------

function jsonResponse_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
