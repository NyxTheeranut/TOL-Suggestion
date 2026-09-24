#!/usr/bin/env python3
"""
Curates the two BMA-West master inventory exports (villages, buildings) into
the compact per-property records the TOL Suggestion dashboard needs --
demographic fields for the search/popup view, plus tag flags and a composite
score for the recommendation system.

Source files (local only, never committed -- see data/README or the repo's
.gitignore):
    data/BMA-West - Village Inventory_2026.xlsb   -> "Total Village&Community"
    data/BMA-West TOL - Building Inventory_2026.xlsx -> "Building Inventory"

Both master sheets are wide (605 / 490 columns) exports of a live pivot
workbook, aggregate row on row 1, real header on row 2, data from row 3.
Columns are found by NAME (via `col()` below), which asserts the header text
it finds there -- so a reordered/inserted column in a future export fails
loudly here instead of silently reading the wrong field into the dashboard.
"Latest month" columns (the *_TOTAL ACTIVE series) are found by regex over
the header row instead of a fixed index, so one more month appearing in a
future export is picked up automatically.

Run `python3 aggregate_suggestion.py --sample` to print a handful of
records for eyeballing before trusting the pipeline; `update_suggestion_sheet.py`
imports `build_output()` from here directly, the same relationship
`update_bb_sheet.py` has with `aggregate_bb.py` in the TOL Tracker project.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import openpyxl
from pyxlsb import open_workbook

HERE = Path(__file__).resolve().parent
VILLAGE_XLSB = HERE / "data" / "BMA-West - Village Inventory_2026.xlsb"
BUILDING_XLSX = HERE / "data" / "BMA-West TOL - Building Inventory_2026.xlsx"

CLOSED_STATUS = "ปิด"

# Scoped to just this PBH cluster (matches TOL Tracker) -- both workbooks
# cover the whole of BMA-West, but this dashboard only needs Pak Kret / Bang
# Bua Thong / Sai Noi. Villages carry this in "HOP_HOZ", buildings in
# "New PBH" -- same exact string in both, confirmed against the raw exports.
PBH_FILTER = "NTB : Pak Kret, Bang Bua Thong, Sai Noi"


def clean(v):
    """Excel formula-error strings (#REF!, #VALUE!, #N/A, ...) show up in a
    handful of columns in these exports -- a broken source reference, not a
    real value. Scrub them to None everywhere rather than letting "#REF!"
    leak into the dashboard as if it were a grade or a date."""
    if isinstance(v, str):
        v = v.strip()
        if v.startswith("#") or v == "":
            return None
    return v


def num(v):
    v = clean(v)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def col(header, index, expected):
    """Returns `index`, after asserting header[index] matches `expected`
    (a literal string, or a compiled regex to `.search()` against). Fails
    loudly on a schema drift instead of silently mis-mapping a column."""
    actual = header[index]
    actual_str = "" if actual is None else str(actual).strip()
    if isinstance(expected, re.Pattern):
        if not expected.search(actual_str):
            raise SystemExit(
                f"column {index} is {actual!r}, expected to match {expected.pattern!r} "
                "-- the source workbook's layout changed, update the index in aggregate_suggestion.py"
            )
    elif actual_str != expected:
        raise SystemExit(
            f"column {index} is {actual!r}, expected {expected!r} "
            "-- the source workbook's layout changed, update the index in aggregate_suggestion.py"
        )
    return index


def find_latest_series(header, value_pattern, pct_pattern):
    """Finds the rightmost (value, pct) column pair whose headers match the
    given regexes, immediately adjacent (value then pct) -- this is how every
    monthly series in these workbooks is laid out. Returns (value_idx, pct_idx)."""
    matches = [
        i for i, h in enumerate(header)
        if h is not None and value_pattern.search(str(h).strip())
    ]
    if not matches:
        raise SystemExit(f"no column matched {value_pattern.pattern!r} -- schema drift?")
    value_idx = matches[-1]
    pct_idx = value_idx + 1
    pct_actual = str(header[pct_idx] or "").strip()
    if not pct_pattern.search(pct_actual):
        raise SystemExit(
            f"expected a %ACTIVE-style column right after index {value_idx} "
            f"({header[value_idx]!r}), found {pct_actual!r} instead"
        )
    return value_idx, pct_idx


# ---------- villages ----------

VILLAGE_ACTIVE_VALUE_RE = re.compile(r"^All [A-Za-z]+ TOTAL ACTIVE$")
VILLAGE_ACTIVE_PCT_RE = re.compile(r"TOTAL ACTIVE$")


def load_villages(path=VILLAGE_XLSB):
    with open_workbook(str(path)) as wb:
        with wb.get_sheet("Total Village&Community") as sheet:
            rows = list(sheet.rows())
    header = [c.v for c in rows[1]]

    i_id = col(header, 5, "VILLAGEID")
    i_name = col(header, 6, "VILLAGE")
    i_district = col(header, 7, "District")
    i_subdistrict = col(header, 8, "Subdistrict")
    i_scab = col(header, 3, "SCAB_NAME")
    i_hop = col(header, 1, "HOP_HOZ")
    i_lat = col(header, 10, "LATITUDE")
    i_lng = col(header, 11, "LONGITUDE")
    i_house = col(header, 19, "HOUSE_ALL")
    i_status = col(header, 390, "หมู่บ้านปิด/ปิดทำกิจกรรมได้/เปิด")
    i_total_port = col(header, 144, "TOTAL PORT")
    i_total_avail = col(header, 145, "TOTAL AVAILABLE")
    i_active, i_active_pct = find_latest_series(header, VILLAGE_ACTIVE_VALUE_RE, VILLAGE_ACTIVE_PCT_RE)
    i_competitor = col(header, 417, "Competitor")
    i_mks_true = col(header, 234, "TRUE OOKLA MKS")
    i_fault_avg = col(header, 363, "Average Fault 3 months")
    i_fault_grade = col(header, 364, "Fault Grade")
    i_churn_3m = col(header, 331, "Total Churn (3mnth)")
    i_churn_rate = col(header, 332, "Churn Rate")
    i_churn_grade = col(header, 396, "Churn Grade")
    i_grade_sale = col(header, 415, "Grade ขาย")
    i_score_sale = col(header, 416, "Score ขาย")
    i_grade_care = col(header, 420, "Grade ดูแล")
    i_score_care = col(header, 421, "Score ดูแล")
    i_final_grade = col(header, 423, "[Final Grade] ขาย-ดูแล")
    i_village_grade = col(header, 425, "Village Grade")
    i_action_group = col(header, 399, "Action Group")
    i_main_group = col(header, 400, "Main Group")
    i_contract_end = col(header, 413, "End")

    out = []
    for row in rows[2:]:
        v = [c.v for c in row]
        vid = v[i_id]
        if vid is None or v[i_name] is None:
            continue
        if clean(v[i_hop]) != PBH_FILTER:
            continue
        out.append({
            "id": str(int(vid)) if isinstance(vid, float) and vid.is_integer() else str(vid),
            "name": clean(v[i_name]),
            "district": clean(v[i_district]),
            "subdistrict": clean(v[i_subdistrict]),
            "scab": clean(v[i_scab]),
            "hopHoz": clean(v[i_hop]),
            "lat": num(v[i_lat]),
            "lng": num(v[i_lng]),
            "status": clean(v[i_status]),
            "houseAll": num(v[i_house]),
            "totalPort": num(v[i_total_port]),
            "totalAvailable": num(v[i_total_avail]),
            "active": num(v[i_active]),
            "activePct": num(v[i_active_pct]),
            "competitor": num(v[i_competitor]),
            "mksTrue": num(v[i_mks_true]),
            "faultAvg": num(v[i_fault_avg]),
            "faultGrade": clean(v[i_fault_grade]),
            "churn3m": num(v[i_churn_3m]),
            "churnRate": num(v[i_churn_rate]),
            "churnGrade": clean(v[i_churn_grade]),
            "gradeSale": clean(v[i_grade_sale]),
            "scoreSale": num(v[i_score_sale]),
            "gradeCare": clean(v[i_grade_care]),
            "scoreCare": num(v[i_score_care]),
            "finalGrade": clean(v[i_final_grade]),
            "villageGrade": clean(v[i_village_grade]) or None,
            "actionGroup": clean(v[i_action_group]),
            "mainGroup": clean(v[i_main_group]),
            "contractEnd": num(v[i_contract_end]),
        })
    return out


# ---------- buildings ----------

BUILDING_ACTIVE_VALUE_RE = re.compile(r"^[A-Za-z]+\d{0,2} TOTAL ACTIVE$")
BUILDING_ACTIVE_PCT_RE = re.compile(r"%Active$")


def load_buildings(path=BUILDING_XLSX):
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb["Building Inventory"]
    header = list(next(ws.iter_rows(min_row=2, max_row=2, values_only=True)))

    i_id = col(header, 13, "BUILDING_ID")
    i_name = col(header, 14, "BUILDING_NAME")
    i_pbh = col(header, 1, "New PBH")
    i_prov = col(header, 4, "PROV_NAMT")
    i_amp = col(header, 5, "AMP_NAMT")
    i_tam = col(header, 6, "TAM_NAMT")
    i_scab = col(header, 3, "SCAB_NAME")
    i_lat = col(header, 15, "BUILDING_LATITUDE")
    i_lng = col(header, 16, "BUILDING_LONGITUDE")
    i_group_type = col(header, 8, "GROUP_TYPE")
    i_subgroup_type = col(header, 9, "SUBGROUP_TYPE")
    i_mdu_model = col(header, 10, "MDU_MODEL")
    i_developer = col(header, 18, "TOP_DEVELOPERS NAME")
    i_floors = col(header, 213, "จำนวนชั้น")
    i_units = col(header, 214, "จำนวนห้องพักอาศัย (Unit)")
    i_occupancy = col(header, 216, "จำนวนห้อง\nที่มีผู้อยู่อาศัยแล้ว(Occupancy)")
    i_total_port = col(header, 161, "TOTAL PORT")
    i_total_avail = col(header, 162, "TOTAL AVAILABLE")
    i_active, i_active_pct = find_latest_series(header, BUILDING_ACTIVE_VALUE_RE, BUILDING_ACTIVE_PCT_RE)
    i_arpu = col(header, 254, "ARPU")
    i_competitor = col(header, 470, "Competitor")
    i_mks_true = col(header, 358, "TRUE OOKLA MKS")
    i_fault_avg = col(header, 311, "Avg Fault Rate")
    i_fault_grade = col(header, 312, "Fault Grade")
    i_churn_3m = col(header, 447, "Avg_Churn (3 Months)")
    i_churn_pct = col(header, 448, "%Churn")
    i_grade_sale = col(header, 468, "Grade ขาย")
    i_score_sale = col(header, 469, "Score ขาย")
    # NOTE: "Grade ดูแล" / "Score ดูแล" / "[Final Grade] ขาย-ดูแล" / "End"
    # (contract end) at indices 473/474/476/466 are #REF!-broken for ~99.5%
    # of building rows in this export (a dead formula reference in the
    # source workbook, verified against real data) -- dropped rather than
    # surfacing near-universal "#REF!" in the dashboard.
    i_group_building = col(header, 483, "Group Building")
    i_caretaker_channel = col(header, 457, "Channel คนดูแล")
    i_caretaker_name = col(header, 458, "คนดูแลตึก")

    out = []
    for row in ws.iter_rows(min_row=3, values_only=True):
        bid = row[i_id]
        if bid is None or row[i_name] is None:
            continue
        if clean(row[i_pbh]) != PBH_FILTER:
            continue
        out.append({
            "id": str(int(bid)) if isinstance(bid, (int, float)) else str(bid),
            "name": clean(row[i_name]),
            "prov": clean(row[i_prov]),
            "amp": clean(row[i_amp]),
            "tam": clean(row[i_tam]),
            "scab": clean(row[i_scab]),
            "lat": num(row[i_lat]),
            "lng": num(row[i_lng]),
            "groupType": clean(row[i_group_type]),
            "subGroupType": clean(row[i_subgroup_type]),
            "mduModel": clean(row[i_mdu_model]),
            "developer": clean(row[i_developer]),
            "floors": num(row[i_floors]),
            "units": num(row[i_units]),
            "occupancy": num(row[i_occupancy]),
            "totalPort": num(row[i_total_port]),
            "totalAvailable": num(row[i_total_avail]),
            "active": num(row[i_active]),
            "activePct": num(row[i_active_pct]),
            "arpu": num(row[i_arpu]),
            "competitor": num(row[i_competitor]),
            "mksTrue": num(row[i_mks_true]),
            "faultAvg": num(row[i_fault_avg]),
            "faultGrade": clean(row[i_fault_grade]),
            "churn3m": num(row[i_churn_3m]),
            "churnPct": num(row[i_churn_pct]),
            "gradeSale": clean(row[i_grade_sale]),
            "scoreSale": num(row[i_score_sale]),
            "groupBuilding": clean(row[i_group_building]),
            "caretakerChannel": clean(row[i_caretaker_channel]),
            "caretakerName": clean(row[i_caretaker_name]),
        })
    return out


# ---------- tags + composite score ----------
# Percentile-based, computed separately for villages and buildings so the two
# property types (very different port/household scales) never get compared
# against each other's thresholds.

def percentile(values, pct):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(vals) - 1)
    if f == c:
        return vals[f]
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def normalize(v, lo, hi):
    if v is None or hi is None or lo is None or hi == lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def add_tags_and_scores(records, size_key):
    """size_key: 'houseAll' for villages, 'units' for buildings."""
    is_open = [r for r in records if r.get("status") != CLOSED_STATUS]

    avail_p75 = percentile([r["totalAvailable"] for r in is_open], 0.75)
    size_p75 = percentile([r[size_key] for r in records], 0.75)
    competitor_p75 = percentile([r["competitor"] for r in records], 0.75)
    fault_p25 = percentile([r["faultAvg"] for r in records], 0.25)

    avail_lo, avail_hi = percentile([r["totalAvailable"] for r in records], 0.05), percentile([r["totalAvailable"] for r in records], 0.95)
    size_lo, size_hi = percentile([r[size_key] for r in records], 0.05), percentile([r[size_key] for r in records], 0.95)
    comp_lo, comp_hi = percentile([r["competitor"] for r in records], 0.05), percentile([r["competitor"] for r in records], 0.95)
    fault_lo, fault_hi = percentile([r["faultAvg"] for r in records], 0.05), percentile([r["faultAvg"] for r in records], 0.95)

    for r in records:
        tags = []
        closed = r.get("status") == CLOSED_STATUS
        if not closed and avail_p75 is not None and (r["totalAvailable"] or 0) >= avail_p75 and avail_p75 > 0:
            tags.append("high_available")
        if size_p75 is not None and (r[size_key] or 0) >= size_p75 and size_p75 > 0:
            tags.append("large")
        if competitor_p75 is not None and (r["competitor"] or 0) >= competitor_p75 and competitor_p75 > 0:
            tags.append("high_competitor")
        if fault_p25 is not None and r["faultAvg"] is not None and r["faultAvg"] <= fault_p25:
            tags.append("low_fault")
        r["tags"] = tags

        avail_n = normalize(r["totalAvailable"], avail_lo, avail_hi)
        size_n = normalize(r[size_key], size_lo, size_hi)
        comp_n = normalize(r["competitor"], comp_lo, comp_hi)
        fault_n = 1.0 - normalize(r["faultAvg"], fault_lo, fault_hi) if r["faultAvg"] is not None else 0.5
        score = 0.35 * avail_n + 0.25 * size_n + 0.25 * comp_n + 0.15 * fault_n
        r["autoScore"] = round(score * 100, 1)
        r["closed"] = closed


def build_output():
    villages = load_villages()
    buildings = load_buildings()
    add_tags_and_scores(villages, "houseAll")
    add_tags_and_scores(buildings, "units")
    return {
        "meta": {
            "villageFile": VILLAGE_XLSB.name,
            "buildingFile": BUILDING_XLSX.name,
            "villageCount": len(villages),
            "buildingCount": len(buildings),
        },
        "villages": villages,
        "buildings": buildings,
    }


def print_sample(out, n=5):
    print(f"Meta: {out['meta']}\n")
    print(f"-- {n} sample villages (of {len(out['villages'])}) --")
    for r in out["villages"][:n]:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    print(f"\n-- {n} sample buildings (of {len(out['buildings'])}) --")
    for r in out["buildings"][:n]:
        print(json.dumps(r, ensure_ascii=False, indent=2))

    for label, key, records in (("village", "houseAll", out["villages"]), ("building", "units", out["buildings"])):
        tag_counts = {}
        for r in records:
            for t in r["tags"]:
                tag_counts[t] = tag_counts.get(t, 0) + 1
        print(f"\n{label} tag counts (of {len(records)}): {tag_counts}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="print sample records + tag counts instead of syncing anywhere")
    ap.add_argument("--sample-size", type=int, default=5)
    args = ap.parse_args()

    if not VILLAGE_XLSB.exists() or not BUILDING_XLSX.exists():
        raise SystemExit(f"expected source files under {HERE / 'data'}")

    out = build_output()
    if args.sample:
        print_sample(out, args.sample_size)
    else:
        print(f"Loaded {out['meta']['villageCount']} villages, {out['meta']['buildingCount']} buildings.")
        print("Run with --sample to inspect records, or use update_suggestion_sheet.py to sync.")


if __name__ == "__main__":
    sys.exit(main())
