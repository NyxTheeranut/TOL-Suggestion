"""
Normalizes aggregate_suggestion.build_output()'s nested dict into the flat
per-tab {header, rows} shape written to the Google Sheet, and back. Villages
and Buildings are already one-row-per-property facts (unlike TOL Tracker's
deeply nested months/districts/buildings payload), so flatten()/reconstruct()
here are close to identity -- the real job is a STABLE column order (so a
dict key insertion-order change in aggregate_suggestion.py can never silently
shift a value into the wrong Sheet column) and marking which columns must be
forced to plain-text format so Sheets doesn't mangle a long numeric-looking
ID or a grade string that happens to look date-ish.

The Apps Script side's `reconstructPayload_` is a hand-ported mirror of
`reconstruct()` below, same relationship as TOL Tracker's.
"""

TAB_NAMES = ["Meta", "Villages", "Buildings", "CalendarTheme", "CalendarPlan"]

VILLAGE_FIELDS = [
    "id", "name", "district", "subdistrict", "scab", "hopHoz", "lat", "lng",
    "status", "closed", "houseAll", "totalPort", "totalAvailable", "active",
    "activePct", "competitor", "mksTrue", "mksFibre3", "mksNt", "competitorMks",
    "competitorSubs", "faultAvg", "faultGrade", "faultTruckRoll",
    "faultTruckRollPct", "faultOther", "faultOtherPct", "churn3m",
    "churnRate", "churnGrade", "churnVolMonthly", "churnInvolMonthly",
    "churnMonthly", "churnMonthlyRate", "gradeSale",
    "scoreSale", "gradeCare", "scoreCare", "finalGrade", "villageGrade",
    "actionGroup", "mainGroup", "contractEnd", "tags", "autoScore",
]

BUILDING_FIELDS = [
    "id", "name", "prov", "amp", "tam", "scab", "lat", "lng", "closed",
    "groupType", "subGroupType", "mduModel", "developer", "floors", "units",
    "occupancy", "totalPort", "totalAvailable", "active", "activePct",
    "arpu", "competitor", "mksTrue", "mksFibre3", "mksNt", "competitorMks",
    "competitorSubs", "faultAvg", "faultGrade", "faultTruckRoll",
    "faultTruckRollPct", "faultOther", "faultOtherPct", "churn3m", "churnPct",
    "churnVolMonthly", "churnInvolMonthly", "churnMonthly", "churnMonthlyRate",
    "gradeSale", "scoreSale",
    "groupBuilding", "caretakerChannel", "caretakerName", "tags", "autoScore",
]

# Columns that must stay plain text in the Sheet -- everything else gets a
# plain-number format. See TOL Tracker's Apps Script for why this matters
# (Sheets otherwise auto-detects a numeric-looking string as a real number,
# stripping precision from a long ID, or a plain string as a date).
TEXT_FIELDS = {
    "id", "name", "district", "subdistrict", "scab", "hopHoz", "status",
    "faultGrade", "churnGrade", "gradeSale", "gradeCare", "finalGrade",
    "villageGrade", "actionGroup", "mainGroup", "tags",
    "prov", "amp", "tam", "groupType", "subGroupType", "mduModel",
    "developer", "groupBuilding", "caretakerChannel", "caretakerName",
    "villageFile", "buildingFile", "syncedAt",
    "monthKey", "kind", "refId", "slot", "tagsCsv", "updatedBy", "updatedAt",
}


def _row_from_record(record, fields):
    row = []
    for f in fields:
        v = record.get(f)
        if f == "tags":
            v = ",".join(v or [])
        elif f == "closed":
            v = 1 if v else 0
        elif v is None:
            v = ""
        row.append(v)
    return row


def _record_from_row(header, row, fields):
    obj = dict(zip(header, row))
    for f in fields:
        if f not in obj:
            obj[f] = None
    obj["tags"] = [t for t in str(obj.get("tags") or "").split(",") if t]
    obj["closed"] = bool(obj.get("closed"))
    for k, v in list(obj.items()):
        if v == "":
            obj[k] = None
    return obj


def flatten(out):
    meta = out["meta"]
    tabs = {
        "Meta": {
            "header": ["villageFile", "buildingFile", "villageCount", "buildingCount", "syncedAt"],
            "rows": [[
                meta.get("villageFile", ""), meta.get("buildingFile", ""),
                meta.get("villageCount", 0), meta.get("buildingCount", 0),
                meta.get("syncedAt", ""),
            ]],
        },
        "Villages": {
            "header": VILLAGE_FIELDS,
            "rows": [_row_from_record(r, VILLAGE_FIELDS) for r in out["villages"]],
        },
        "Buildings": {
            "header": BUILDING_FIELDS,
            "rows": [_row_from_record(r, BUILDING_FIELDS) for r in out["buildings"]],
        },
    }
    # CalendarTheme / CalendarPlan are owned by the live page (saveCalendarTheme /
    # saveCalendarDay in the Apps Script), not by this local sync -- but if the
    # Sheet doesn't have them yet (first-ever sync), create them empty so
    # myData never has to special-case a missing tab.
    if "calendarTheme" in out:
        tabs["CalendarTheme"] = out["calendarTheme"]
    if "calendarPlan" in out:
        tabs["CalendarPlan"] = out["calendarPlan"]
    return tabs


def reconstruct(tabs):
    meta_row = dict(zip(tabs["Meta"]["header"], tabs["Meta"]["rows"][0])) if tabs["Meta"]["rows"] else {}
    villages = [_record_from_row(tabs["Villages"]["header"], row, VILLAGE_FIELDS) for row in tabs["Villages"]["rows"]]
    buildings = [_record_from_row(tabs["Buildings"]["header"], row, BUILDING_FIELDS) for row in tabs["Buildings"]["rows"]]
    return {
        "meta": meta_row,
        "villages": villages,
        "buildings": buildings,
        "calendarTheme": tabs.get("CalendarTheme", {"header": [], "rows": []}),
        "calendarPlan": tabs.get("CalendarPlan", {"header": [], "rows": []}),
    }
