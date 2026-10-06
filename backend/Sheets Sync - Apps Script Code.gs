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
 *                  A day's regenerate replaces just that day's rows, never
 *                  the whole tab -- see saveCalendarDay_ below.
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
 *   saveCalendarTheme / saveCalendarDay / clearCalendarMonth /
 *   saveCalendarMonth / saveCalendarSlot / touchCalendarSlot /
 *   setCalendarSlotLock / clearCalendarSlot -> any allow-
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
  "status", "closed", "houseAll", "totalPort", "totalAvailable", "active",
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
  "occupancy", "totalPort", "totalAvailable", "active", "activePrevMonth",
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
  "id", "name", "district", "subdistrict", "scab", "hopHoz", "status",
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

    if (body.action === "saveCalendarDay") {
      var email2 = verifyIdToken_(body.idToken);
      if (!email2 || !isAllowedUser_(email2)) return jsonResponse_({ ok: false, error: "not_signed_in" });
      saveCalendarDay_(body.monthKey, body.day, body.picks || [], email2);
      return jsonResponse_({ ok: true });
    }

    if (body.action === "saveCalendarMonth" || body.action === "saveCalendarSlot" ||
        body.action === "touchCalendarSlot" || body.action === "setCalendarSlotLock" ||
        body.action === "clearCalendarSlot") {
      var emailS = verifyIdToken_(body.idToken);
      if (!emailS || !isAllowedUser_(emailS)) return jsonResponse_({ ok: false, error: "not_signed_in" });
      if (body.action === "saveCalendarMonth") saveCalendarMonth_(body.monthKey, body.plan || {}, body.tags || [], emailS);
      else if (body.action === "saveCalendarSlot") saveCalendarSlot_(body.slot, body.monthKey, body.tags || [], body.plan || {}, emailS);
      else if (body.action === "touchCalendarSlot") touchCalendarSlot_(body.slot, emailS);
      else if (body.action === "setCalendarSlotLock") setCalendarSlotLock_(body.slot, !!body.locked, emailS);
      else clearCalendarSlot_(body.slot, emailS);
      return jsonResponse_({ ok: true });
    }

    if (body.action === "clearCalendarMonth") {
      var email3 = verifyIdToken_(body.idToken);
      if (!email3 || !isAllowedUser_(email3)) return jsonResponse_({ ok: false, error: "not_signed_in" });
      clearCalendarMonth_(body.monthKey, email3);
      return jsonResponse_({ ok: true });
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
  tabs.CalendarPlan = filterOwnRows_(tabs.CalendarPlan, "email", email);
  tabs.CalendarTheme = filterOwnRows_(tabs.CalendarTheme, "updatedBy", email);
  tabs.CalendarSlots = ownSlotsTab_(email); // saved plans; expired unlocked ones are already left out
  // Sends the raw tabs, NOT reconstructPayload_(tabs) -- rebuilding the full
  // nested shape (~1,500 property rows into tagged/typed objects) is real
  // CPU work, and doing it here means every sign-in pays for it inside
  // Apps Script's slower, quota-metered runtime before the viewer sees
  // anything at all (this was almost certainly why sign-in was timing out).
  // The browser does the identical reconstruction (reconstructPayload_
  // ported verbatim into index.html) in its own fast JS engine instead --
  // same "ship raw, reconstruct client-side" split TOL Tracker's myBbData_
  // already uses, and for the same reason.
  return { ok: true, email: email, tabs: tabs };
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
  pruneExpiredSlots_();
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

// ---------- CalendarSlots: three saved plans per viewer ----------
// A slot is a snapshot of one month's plan (every day's picks + the month's
// theme tags) the viewer chose to keep, so it can be re-opened later even
// after the working plan has aged out of the retention window above. Each
// viewer has CALENDAR_SLOT_COUNT slots; a slot that is NOT locked and hasn't
// been saved or opened for CALENDAR_SLOT_TTL_DAYS is deleted automatically;
// a locked slot is kept until its owner unlocks or clears it.
var CALENDAR_SLOT_HEADER = ["email", "slot", "monthKey", "savedAt", "lastOpenedAt", "locked", "tagsCsv", "planJson"];
var CALENDAR_SLOT_COUNT = 3;
var CALENDAR_SLOT_TTL_DAYS = 30;
var CALENDAR_SLOT_MAX_JSON = 45000; // Sheets caps a cell at 50,000 characters

function isLockedFlag_(v) { return v === true || v === 1 || String(v) === "1" || String(v).toLowerCase() === "true"; }

function slotExpired_(row) {
  if (isLockedFlag_(row[5])) return false;
  var t = Date.parse(row[4]);
  return isNaN(t) || (Date.now() - t) > CALENDAR_SLOT_TTL_DAYS * 86400000;
}

function readSlotRows_() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("CalendarSlots");
  if (!sheet) return [];
  return normalizeRows_(sheet.getDataRange().getValues().slice(1), CALENDAR_SLOT_HEADER.length);
}

// Every column is text: a month key / slot number / JSON blob must come back
// exactly as written, never coerced into a number or date by Sheets.
function writeSlotRows_(rows) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("CalendarSlots") || ss.insertSheet("CalendarSlots");
  var width = CALENDAR_SLOT_HEADER.length;
  sheet.clearContents();
  sheet.getRange(1, 1, sheet.getMaxRows(), width).setNumberFormat("@");
  sheet.getRange(1, 1, 1, width).setValues([CALENDAR_SLOT_HEADER]);
  if (rows.length) {
    var text = rows.map(function (r) { return r.map(function (c) { return c == null ? "" : String(c); }); });
    sheet.getRange(2, 1, text.length, width).setValues(text);
  }
  sheet.setFrozenRows(1);
}

function pruneExpiredSlots_() {
  var rows = readSlotRows_();
  var kept = rows.filter(function (r) { return !slotExpired_(r); });
  if (kept.length !== rows.length) writeSlotRows_(kept);
}

function findSlotIdx_(rows, slot, email) {
  for (var i = 0; i < rows.length; i++) {
    if (rows[i][0] === email && String(rows[i][1]) === String(slot)) return i;
  }
  return -1;
}

function checkSlotNumber_(slot) {
  slot = Number(slot);
  if (!(slot >= 1 && slot <= CALENDAR_SLOT_COUNT) || slot !== Math.floor(slot)) throw new Error("bad_slot");
  return slot;
}

// The viewer's own, not-yet-expired slots, shaped like any other tab.
function ownSlotsTab_(email) {
  return {
    header: CALENDAR_SLOT_HEADER,
    rows: readSlotRows_().filter(function (r) { return r[0] === email && !slotExpired_(r); }),
  };
}

function saveCalendarSlot_(slot, monthKey, tags, plan, email) {
  slot = checkSlotNumber_(slot);
  if (!monthKey) throw new Error("monthKey required");
  var planJson = JSON.stringify(plan || {});
  if (planJson.length > CALENDAR_SLOT_MAX_JSON) throw new Error("plan_too_large");
  pruneExpiredSlots_();
  var rows = readSlotRows_();
  var i = findSlotIdx_(rows, slot, email);
  if (i !== -1 && isLockedFlag_(rows[i][5])) throw new Error("slot_locked");
  var now = new Date().toISOString();
  var row = [email, String(slot), String(monthKey), now, now, "0", (tags || []).join(","), planJson];
  if (i === -1) rows.push(row); else rows[i] = row;
  writeSlotRows_(rows);
}

// Opening a slot counts as "used": restarts its 30-day clock.
function touchCalendarSlot_(slot, email) {
  slot = checkSlotNumber_(slot);
  var rows = readSlotRows_();
  var i = findSlotIdx_(rows, slot, email);
  if (i === -1) throw new Error("slot_empty");
  rows[i][4] = new Date().toISOString();
  writeSlotRows_(rows);
}

function setCalendarSlotLock_(slot, locked, email) {
  slot = checkSlotNumber_(slot);
  var rows = readSlotRows_();
  var i = findSlotIdx_(rows, slot, email);
  if (i === -1) throw new Error("slot_empty");
  rows[i][5] = locked ? "1" : "0";
  rows[i][4] = new Date().toISOString();
  writeSlotRows_(rows);
}

function clearCalendarSlot_(slot, email) {
  slot = checkSlotNumber_(slot);
  var rows = readSlotRows_();
  var i = findSlotIdx_(rows, slot, email);
  if (i === -1) return;
  if (isLockedFlag_(rows[i][5])) throw new Error("slot_locked");
  rows.splice(i, 1);
  writeSlotRows_(rows);
}

// Replaces the caller's ENTIRE plan for one month in one write (used when a
// slot is opened) -- far cheaper than ~30 saveCalendarDay calls. `plan` is
// {day: [{slot, kind, refId, name}, ...]}.
function saveCalendarMonth_(monthKey, plan, tags, email) {
  if (!monthKey) throw new Error("monthKey required");
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("CalendarPlan");
  if (!sheet) {
    sheet = ss.insertSheet("CalendarPlan");
    sheet.appendRow(CALENDAR_PLAN_HEADER);
    sheet.setFrozenRows(1);
  }
  var width = CALENDAR_PLAN_HEADER.length;
  var kept = normalizeRows_(sheet.getDataRange().getValues().slice(1), width).filter(function (row) {
    return !(String(row[0]) === String(monthKey) && row[5] === email);
  });
  Object.keys(plan || {}).forEach(function (day) {
    (plan[day] || []).forEach(function (p) {
      kept.push([monthKey, Number(day), p.slot, p.kind, p.refId, email, p.name || ""]);
    });
  });
  sheet.clearContents();
  formatPlanTextColumns_(sheet);
  textifyPlanRows_(kept);
  sheet.getRange(1, 1, 1, width).setValues([CALENDAR_PLAN_HEADER]);
  if (kept.length) sheet.getRange(2, 1, kept.length, width).setValues(kept);
  sheet.setFrozenRows(1);
  saveCalendarTheme_(monthKey, tags || [], email); // also runs the retention prune
}

function saveCalendarDay_(monthKey, day, picks, email) {
  // picks: [{slot, kind, refId}, ...] -- replaces every existing row THIS
  // viewer owns for this (monthKey, day) with exactly these, in one
  // rewrite of the tab (CalendarPlan stays small -- a season's worth of
  // days, times however many people are planning, is still only a few
  // hundred rows -- so reading it whole here is cheap). Another viewer's
  // rows for the same day are left completely alone.
  if (!monthKey || !day) throw new Error("monthKey and day required");
  pruneOldCalendarData_();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("CalendarPlan");
  if (!sheet) {
    sheet = ss.insertSheet("CalendarPlan");
    sheet.appendRow(CALENDAR_PLAN_HEADER);
    sheet.setFrozenRows(1);
  }
  var width = CALENDAR_PLAN_HEADER.length;
  var data = sheet.getDataRange().getValues();
  var kept = normalizeRows_(data.slice(1), width).filter(function (row) {
    return !(String(row[0]) === String(monthKey) && Number(row[1]) === Number(day) && row[5] === email);
  });
  picks.forEach(function (p) {
    kept.push([monthKey, day, p.slot, p.kind, p.refId, email, p.name || ""]);
  });
  sheet.clearContents();
  formatPlanTextColumns_(sheet);
  textifyPlanRows_(kept);
  sheet.getRange(1, 1, 1, width).setValues([CALENDAR_PLAN_HEADER]);
  if (kept.length) sheet.getRange(2, 1, kept.length, width).setValues(kept);
  sheet.setFrozenRows(1);
}

// Manual "Clear month" from the page -- wipes exactly the calling viewer's
// own CalendarPlan rows and CalendarTheme row for one month, on demand
// rather than waiting for it to age out of the retention window above.
// Never touches another viewer's plan for that same month.
function clearCalendarMonth_(monthKey, email) {
  if (!monthKey) throw new Error("monthKey required");
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  var planSheet = ss.getSheetByName("CalendarPlan");
  if (planSheet) {
    var pdata = planSheet.getDataRange().getValues();
    if (pdata.length >= 2) {
      var pkept = normalizeRows_(pdata.slice(1), CALENDAR_PLAN_HEADER.length).filter(function (row) {
        return !(String(row[0]) === String(monthKey) && row[5] === email);
      });
      planSheet.clearContents();
      formatPlanTextColumns_(planSheet);
      textifyPlanRows_(pkept);
      planSheet.getRange(1, 1, 1, CALENDAR_PLAN_HEADER.length).setValues([CALENDAR_PLAN_HEADER]);
      if (pkept.length) planSheet.getRange(2, 1, pkept.length, CALENDAR_PLAN_HEADER.length).setValues(pkept);
      planSheet.setFrozenRows(1);
    }
  }

  var themeSheet = ss.getSheetByName("CalendarTheme");
  if (themeSheet) {
    var tdata = themeSheet.getDataRange().getValues();
    if (tdata.length >= 2) {
      var tkept = normalizeRows_(tdata.slice(1), CALENDAR_THEME_HEADER.length).filter(function (row) {
        return !(String(row[0]) === String(monthKey) && row[2] === email);
      });
      themeSheet.clearContents();
      themeSheet.getRange(1, 1, 1, CALENDAR_THEME_HEADER.length).setValues([CALENDAR_THEME_HEADER]);
      if (tkept.length) themeSheet.getRange(2, 1, tkept.length, CALENDAR_THEME_HEADER.length).setValues(tkept);
      themeSheet.setFrozenRows(1);
    }
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
