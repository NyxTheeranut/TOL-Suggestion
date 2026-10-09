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
import csv
import json
import re
import sys
from pathlib import Path

import openpyxl
from pyxlsb import open_workbook

HERE = Path(__file__).resolve().parent
REPO_DIR = HERE.parent  # this script lives in sync/, data/ is a sibling of sync/
VILLAGE_XLSB = REPO_DIR / "data" / "BMA-West - Village Inventory_2026.xlsb"
BUILDING_XLSX = REPO_DIR / "data" / "BMA-West TOL - Building Inventory_2026.xlsx"

# Shared with the Dashboard's other projects (outside this repo) -- L2
# Discount Map's own update_l2_sheet.py reads these same two files. There's
# no ID shared between that project's source data and this one's, so
# village NAME is the only link -- same assumption that project's own script
# already makes for its village-name display.
DASHBOARD_DIR = REPO_DIR.parent

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


def sum_or_none(*vals):
    """Sums whichever of `vals` are present, treating a missing one as 0 --
    but returns None (not 0) if EVERY value is missing, so "no data" isn't
    confused with "genuinely zero"."""
    present = [v for v in vals if v is not None]
    return sum(present) if present else None

NETWORK_TECHS = ("FTTH", "FTTB", "FTTC")


def network_of(labels, ports_by_tech):
    """Canonical "FTTH,FTTB,FTTC"-style string of the access technologies a
    place has, or None. Starts from the workbook's own network label (first
    label that names a technology wins -- the village file has both
    "*Revised Network" and the older "Network", the revised one is blank/"None"
    on some rows the old one still fills), then adds any technology that has
    ports installed: a handful of rows are labelled e.g. "FTTB ONLY" yet carry
    FTTH ports, and the port picker in the detail card must never offer a
    technology the label pill leaves out. "Outdoor"/"None" name no technology."""
    found = set()
    for label in labels:
        toks = {t for t in NETWORK_TECHS if t in str(label or "").upper()}
        if toks:
            found = toks
            break
    for tech, total in ports_by_tech.items():
        if total:
            found.add(tech)
    return ",".join(t for t in NETWORK_TECHS if t in found) or None


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


def find_prev_value(header, value_pattern):
    """Same matching as find_latest_series, but returns the SECOND-to-last
    month's value column index (for a month-over-month delta) -- or None if
    only one month of the series exists yet."""
    matches = [
        i for i, h in enumerate(header)
        if h is not None and value_pattern.search(str(h).strip())
    ]
    return matches[-2] if len(matches) >= 2 else None


def find_latest_paired(header, vol_pattern, vol_prefix, invol_prefix):
    """Finds the rightmost "Vol ..." column, then looks up its exact
    "Invol ..." counterpart by swapping the prefix on that same matched
    name -- e.g. "Vol Churn Aug-26" -> "Invol Churn Aug-26" -- rather than
    assuming a fixed column offset between the two blocks (they aren't
    adjacent -- villages' Invol block sits 3 columns after each Vol column,
    buildings' Invol block is an entirely separate range further along the
    sheet). Self-verifying: if no exact match exists, the invol side comes
    back None instead of silently reading the wrong column.
    Returns (vol_idx, invol_idx) -- either may be None."""
    matches = [
        i for i, h in enumerate(header)
        if h is not None and vol_pattern.search(str(h).strip())
    ]
    if not matches:
        return None, None
    vol_idx = matches[-1]
    vol_name = str(header[vol_idx]).strip()
    invol_name = invol_prefix + vol_name[len(vol_prefix):]
    invol_idx = next((i for i, h in enumerate(header) if h is not None and str(h).strip() == invol_name), None)
    return vol_idx, invol_idx


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
    # FTTH-specific, not the "TOTAL PORT"/"TOTAL AVAILABLE" columns a few
    # dozen over -- those sum ports across every access technology the
    # village has (FTTH+FTTB+FTTC), which massively overstates available
    # capacity for this FTTH sales tool wherever a village also has legacy
    # FTTC/FTTB build-out (confirmed against real data: a village with
    # "*Revised Network" = FTTH,FTTB,FTTC showed FTTH TOTAL PORT 904 / FTTH
    # AVAILABLE 344, but the combined TOTAL PORT/TOTAL AVAILABLE columns
    # read 2502/1908).
    i_total_port = col(header, 23, "FTTH TOTAL PORT")
    i_total_avail = col(header, 24, "FTTH AVAILABLE")
    # Other access technologies + the all-technology totals, for the detail
    # card's port picker (FTTH stays the default and what scoring uses).
    i_network_raw = col(header, 21, "Network")
    i_network_rev = col(header, 22, "*Revised Network")
    i_fttb_port = col(header, 72, "FTTB TOTAL")
    i_fttb_avail = col(header, 73, "FTTB AVAILABLE")
    i_fttc_port = col(header, 108, "FTTC TOTAL PORT")
    i_fttc_avail = col(header, 109, "FTTC AVAILABLE")
    i_all_port = col(header, 144, "TOTAL PORT")
    i_all_avail = col(header, 145, "TOTAL AVAILABLE")
    i_active, i_active_pct = find_latest_series(header, VILLAGE_ACTIVE_VALUE_RE, VILLAGE_ACTIVE_PCT_RE)
    i_active_prev = find_prev_value(header, VILLAGE_ACTIVE_VALUE_RE)
    i_competitor = col(header, 417, "Competitor")
    i_mks_true = col(header, 234, "TRUE OOKLA MKS")
    i_mks_fibre3 = col(header, 232, "Fiber3 OOKLA MKS")
    i_mks_nt = col(header, 236, "NT OOKLA MKS")
    i_true_avg_dl = col(header, 221, "avg_dl")
    i_true_max_dl = col(header, 222, "max_dl")
    i_fibre3_avg_dl = col(header, 191, "avg_dl")
    i_fibre3_max_dl = col(header, 192, "max_dl")
    i_fault_avg = col(header, 363, "Average Fault 3 months")
    i_fault_grade = col(header, 364, "Fault Grade")
    i_case_mgmt = col(header, 365, "Case Management")
    i_fcr = col(header, 366, "FCR")
    i_network_event = col(header, 367, "Network Event")
    i_fault_others = col(header, 368, "Others")
    i_second_tier = col(header, 369, "Second Tier")
    i_truck_roll = col(header, 370, "Truck Roll")
    i_truck_roll_pct = col(header, 371, "%Truck Roll")
    i_churn_3m = col(header, 331, "Total Churn (3mnth)")
    i_churn_rate = col(header, 332, "Churn Rate")
    i_churn_grade = col(header, 396, "Churn Grade")
    i_vol_churn, i_invol_churn = find_latest_paired(
        header, re.compile(r"^Vol Churn [A-Za-z]+-\d{2}$"), "Vol Churn ", "Invol Churn ",
    )
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

        active = num(v[i_active])
        active_prev = num(v[i_active_prev]) if i_active_prev is not None else None
        mks_true = num(v[i_mks_true])
        mks_fibre3 = num(v[i_mks_fibre3])
        mks_nt = num(v[i_mks_nt])
        fault_truck_roll = num(v[i_truck_roll])
        fault_other = sum_or_none(num(v[i_case_mgmt]), num(v[i_fcr]), num(v[i_network_event]), num(v[i_fault_others]), num(v[i_second_tier]))
        vol_churn = num(v[i_vol_churn]) if i_vol_churn is not None else None
        invol_churn = num(v[i_invol_churn]) if i_invol_churn is not None else None
        churn_monthly = sum_or_none(vol_churn, invol_churn)
        # Competitor MKS scoped to just Fiber3 (3BB's fiber brand) + NT --
        # not AIS, not the raw "Competitor" count column -- per direct
        # instruction; True's own MKS is kept exactly as given, not
        # renormalized against this narrower competitor set.
        competitor_mks = sum_or_none(mks_fibre3, mks_nt)
        # Backs out an implied total addressable (OOKLA-sampled) market from
        # True's own known active count and share, then applies the
        # competitor share to that same base -- standard market-sizing math,
        # not a business-defined metric from the source workbook.
        competitor_subs = (active / mks_true) * competitor_mks if (active is not None and mks_true) else None

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
            "network": network_of(
                (v[i_network_rev], v[i_network_raw]),
                {"FTTH": num(v[i_total_port]), "FTTB": num(v[i_fttb_port]), "FTTC": num(v[i_fttc_port])},
            ),
            "totalPort": num(v[i_total_port]),
            "totalAvailable": num(v[i_total_avail]),
            "fttbPort": num(v[i_fttb_port]),
            "fttbAvailable": num(v[i_fttb_avail]),
            "fttcPort": num(v[i_fttc_port]),
            "fttcAvailable": num(v[i_fttc_avail]),
            "allPort": num(v[i_all_port]),
            "allAvailable": num(v[i_all_avail]),
            "active": active,
            "activePrevMonth": active_prev,
            "activePct": num(v[i_active_pct]),
            "competitor": num(v[i_competitor]),
            "mksTrue": mks_true,
            "mksFibre3": mks_fibre3,
            "mksNt": mks_nt,
            "competitorMks": competitor_mks,
            "competitorSubs": round(competitor_subs, 1) if competitor_subs is not None else None,
            "trueAvgDl": num(v[i_true_avg_dl]),
            "trueMaxDl": num(v[i_true_max_dl]),
            "fibre3AvgDl": num(v[i_fibre3_avg_dl]),
            "fibre3MaxDl": num(v[i_fibre3_max_dl]),
            "faultAvg": num(v[i_fault_avg]),
            "faultGrade": clean(v[i_fault_grade]),
            "faultTruckRoll": fault_truck_roll,
            "faultTruckRollPct": num(v[i_truck_roll_pct]),
            "faultOther": fault_other,
            # Same denominator as the source's own "%Truck Roll" column
            # (verified against real data: matches TruckRoll/active to 5
            # decimal places, using the PRIOR month's active count since the
            # fault block runs a month behind the active-subscriber series)
            # -- NOT other/(other+truckRoll), which was a different,
            # incomparable composition-of-fault-types ratio on a totally
            # different scale (40-75% vs. Truck Roll Rate's typical 1-6%).
            "faultOtherPct": (fault_other / active_prev) if (fault_other is not None and active_prev) else ((fault_other / active) if (fault_other is not None and active) else None),
            "churn3m": num(v[i_churn_3m]),
            "churnRate": num(v[i_churn_rate]),
            "churnGrade": clean(v[i_churn_grade]),
            "churnVolMonthly": vol_churn,
            "churnInvolMonthly": invol_churn,
            "churnMonthly": churn_monthly,
            "churnMonthlyRate": (churn_monthly / active) if (churn_monthly is not None and active) else None,
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
    # FTTH-specific, same reasoning as the village sheet -- "TOTAL PORT"/
    # "TOTAL AVAILABLE" a few dozen columns over sum every access
    # technology (FTTH+FTTB+FTTC), overstating available capacity.
    i_total_port = col(header, 20, "FTTH TOTAL")
    i_total_avail = col(header, 21, "FTTH TOTAL AVAILABLE")
    i_network_rev = col(header, 19, "Network_Rev")
    i_fttb_port = col(header, 69, "FTTB TOTAL")
    i_fttb_avail = col(header, 70, "FTTB TOTAL AVAILABLE")
    i_fttc_port = col(header, 115, "FTTC TOTAL TAP PORTS")
    i_fttc_avail = col(header, 116, "FTTC TOTAL AVAILABLE")
    i_all_port = col(header, 161, "TOTAL PORT")
    i_all_avail = col(header, 162, "TOTAL AVAILABLE")
    i_active, i_active_pct = find_latest_series(header, BUILDING_ACTIVE_VALUE_RE, BUILDING_ACTIVE_PCT_RE)
    i_active_prev = find_prev_value(header, BUILDING_ACTIVE_VALUE_RE)
    i_arpu = col(header, 254, "ARPU")
    i_competitor = col(header, 470, "Competitor")
    i_mks_true = col(header, 358, "TRUE OOKLA MKS")
    i_mks_fibre3 = col(header, 356, "Fiber3 OOKLA MKS")
    i_mks_nt = col(header, 360, "NT OOKLA MKS")
    i_true_avg_dl = col(header, 366, "TRUE Avg Speed")
    i_true_max_dl = col(header, 367, "TRUE Max Speed")
    i_fibre3_avg_dl = col(header, 328, "Avg Speed")
    i_fibre3_max_dl = col(header, 329, "Max Speed")
    i_fault_avg = col(header, 311, "Avg Fault Rate")
    i_fault_grade = col(header, 312, "Fault Grade")
    i_case_mgmt = col(header, 313, "Case Management")
    i_fcr = col(header, 314, "FCR")
    i_network_event = col(header, 315, "Network Event")
    i_second_tier = col(header, 316, "Second Tier")
    i_truck_roll = col(header, 317, "Truck Roll")
    i_truck_roll_pct = col(header, 318, "%Truck Roll")
    i_churn_3m = col(header, 447, "Avg_Churn (3 Months)")
    i_churn_pct = col(header, 448, "%Churn")
    i_vol_churn, i_invol_churn = find_latest_paired(
        header, re.compile(r"^Vol_[A-Za-z]+\d{2}$"), "Vol_", "Invol_",
    )
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

        active = num(row[i_active])
        active_prev = num(row[i_active_prev]) if i_active_prev is not None else None
        mks_true = num(row[i_mks_true])
        mks_fibre3 = num(row[i_mks_fibre3])
        mks_nt = num(row[i_mks_nt])
        fault_truck_roll = num(row[i_truck_roll])
        # No separate "Others" raw column here (unlike villages) -- the
        # remaining 4 fault-cause categories stand in for it.
        fault_other = sum_or_none(num(row[i_case_mgmt]), num(row[i_fcr]), num(row[i_network_event]), num(row[i_second_tier]))
        vol_churn = num(row[i_vol_churn]) if i_vol_churn is not None else None
        invol_churn = num(row[i_invol_churn]) if i_invol_churn is not None else None
        churn_monthly = sum_or_none(vol_churn, invol_churn)
        competitor_mks = sum_or_none(mks_fibre3, mks_nt)
        competitor_subs = (active / mks_true) * competitor_mks if (active is not None and mks_true) else None

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
            "network": network_of(
                (row[i_network_rev],),
                {"FTTH": num(row[i_total_port]), "FTTB": num(row[i_fttb_port]), "FTTC": num(row[i_fttc_port])},
            ),
            "totalPort": num(row[i_total_port]),
            "totalAvailable": num(row[i_total_avail]),
            "fttbPort": num(row[i_fttb_port]),
            "fttbAvailable": num(row[i_fttb_avail]),
            "fttcPort": num(row[i_fttc_port]),
            "fttcAvailable": num(row[i_fttc_avail]),
            "allPort": num(row[i_all_port]),
            "allAvailable": num(row[i_all_avail]),
            "active": active,
            "activePrevMonth": active_prev,
            "activePct": num(row[i_active_pct]),
            "arpu": num(row[i_arpu]),
            "competitor": num(row[i_competitor]),
            "mksTrue": mks_true,
            "mksFibre3": mks_fibre3,
            "mksNt": mks_nt,
            "competitorMks": competitor_mks,
            "competitorSubs": round(competitor_subs, 1) if competitor_subs is not None else None,
            "trueAvgDl": num(row[i_true_avg_dl]),
            "trueMaxDl": num(row[i_true_max_dl]),
            "fibre3AvgDl": num(row[i_fibre3_avg_dl]),
            "fibre3MaxDl": num(row[i_fibre3_max_dl]),
            "faultAvg": num(row[i_fault_avg]),
            "faultGrade": clean(row[i_fault_grade]),
            "faultTruckRoll": fault_truck_roll,
            "faultTruckRollPct": num(row[i_truck_roll_pct]),
            "faultOther": fault_other,
            # Same denominator as the source's own "%Truck Roll" column --
            # see the matching comment in curate_villages().
            "faultOtherPct": (fault_other / active_prev) if (fault_other is not None and active_prev) else ((fault_other / active) if (fault_other is not None and active) else None),
            "churn3m": num(row[i_churn_3m]),
            "churnPct": num(row[i_churn_pct]),
            "churnVolMonthly": vol_churn,
            "churnInvolMonthly": invol_churn,
            "churnMonthly": churn_monthly,
            "churnMonthlyRate": (churn_monthly / active) if (churn_monthly is not None and active) else None,
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


LOW_FAULT_RATE = 0.04


def fault_rate(r):
    """All faults (truck roll + other) per active subscriber, or None when it
    can't be worked out (no fault figures, or no active subscribers)."""
    total = sum_or_none(r.get("faultTruckRoll"), r.get("faultOther"))
    active = r.get("active")
    if total is None or not active:
        return None
    return total / active


def add_tags_and_scores(records, size_key):
    """size_key: 'houseAll' for villages, 'units' for buildings."""
    is_open = [r for r in records if r.get("status") != CLOSED_STATUS]

    avail_p75 = percentile([r["totalAvailable"] for r in is_open], 0.75)
    size_p75 = percentile([r[size_key] for r in records], 0.75)
    # (the "low_fault" tag no longer uses a percentile -- see fault_rate())

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
        # "Fault < 4%": every fault type together (truck roll + the other kinds)
        # over active subscribers is under 4%. A place with no fault data, or no
        # active subscribers to divide by, counts as low fault too.
        rate = fault_rate(r)
        if rate is None or rate < LOW_FAULT_RATE:
            tags.append("low_fault")
        r["tags"] = tags

        avail_n = normalize(r["totalAvailable"], avail_lo, avail_hi)
        size_n = normalize(r[size_key], size_lo, size_hi)
        comp_n = normalize(r["competitor"], comp_lo, comp_hi)
        fault_n = 1.0 - normalize(r["faultAvg"], fault_lo, fault_hi) if r["faultAvg"] is not None else 0.5
        score = 0.35 * avail_n + 0.25 * size_n + 0.25 * comp_n + 0.15 * fault_n
        r["autoScore"] = round(score * 100, 1)
        r["closed"] = closed


def _find_l2_xlsx():
    for folder in ("L2", "Config", "."):
        matches = sorted((DASHBOARD_DIR / folder).glob("*Project Atlas L2*.xlsx"), reverse=True)
        if matches:
            return matches[0]
    return None


def _find_village_lookup_file():
    for folder in (Path("TOL Tracker") / "Data", "Config", "."):
        candidate = DASHBOARD_DIR / folder / "Active FTTH In Village_BMA-West.TXT"
        if candidate.exists():
            return candidate
    return None


def load_l2_by_village():
    """Optional enrichment: which villages have an active-discount L2
    splitter, and that splitter's own discount terms -- matched by village
    NAME against the exact same two source files L2 Discount Map's own
    update_l2_sheet.py reads (there's no ID shared between the two
    projects' source data). Returns ({}, {}) (skip enrichment, not an
    error) if those files aren't present on this machine -- L2 is a bonus
    layer here, not something TOL Suggestion depends on to function.

    Mirrors that project's own filtering logic (a Condition-table lookup
    resolving to a nonzero discount) -- see that project's
    update_l2_sheet.py for the fuller original. The per-package MKT
    discount table is deduped to once per (arch, port, arpaGroup, nad)
    combo actually used at a village, keyed by condKey, rather than
    repeated on every point -- a housing estate can have 40+ points that
    almost all share the same 1-2 combos, and embedding the full 9-package
    table on each one blew a single village's Sheet cell well past
    Google Sheets' 50,000-character-per-cell limit (confirmed against real
    data: one village hit 62,779 characters undeduped)."""
    xlsx = _find_l2_xlsx()
    lookup_file = _find_village_lookup_file()
    if not xlsx or not lookup_file:
        print("  (L2 source files not found on this machine -- skipping L2 enrichment)")
        return {}, {}

    village_lookup = {}
    with open(lookup_file, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter="^")
        header = next(reader)
        idx_l2 = header.index("Vill_SPLITTER_L2")
        idx_vname = header.index("GIS_VILL_NAME")
        for row in reader:
            if len(row) <= max(idx_l2, idx_vname):
                continue
            l2, vname = row[idx_l2].strip(), row[idx_vname].strip()
            if l2 and vname and l2 not in village_lookup:
                village_lookup[l2] = vname

    wb = openpyxl.load_workbook(xlsx, data_only=True, read_only=True)
    conditions = []
    for r in wb["Condition"].iter_rows(min_row=3, values_only=True):
        if r[1] is None:
            continue
        # Each condition row carries its own 9-package MKT sub-table (code,
        # description, discount %, normal price, special/discounted price) --
        # same layout L2 Discount Map's own load_conditions() reads.
        mkts = []
        idx = 6
        for _ in range(9):
            code, desc, disc, normal, special = r[idx], r[idx + 1], r[idx + 2], r[idx + 3], r[idx + 4]
            mkts.append({
                "code": code, "desc": desc,
                "disc": round(disc, 4) if disc is not None else None,
                "normal": normal,
                "special": round(special, 2) if special is not None else None,
            })
            idx += 5
        conditions.append({"arch": r[1], "arpaGroup": r[2], "port": r[3], "nad": r[4], "discPct": r[5], "mkts": mkts})

    def find_condition(arch, arpa_group, port, nad):
        for c in conditions:
            if c["arch"] == arch and c["arpaGroup"] == arpa_group and c["port"] == port and c["nad"] == nad:
                return c
        return None

    by_village = {}
    mkts_by_village = {}  # vname -> {condKey: [mkts...]}, deduped
    # Sheet name is hardcoded in L2 Discount Map's own script too, regardless
    # of the *file*name's month -- the source Atlas workbook doesn't rename
    # this internal sheet month to month.
    for row in wb["L2 Data vAug"].iter_rows(min_row=2, values_only=True):
        arch, arpa_group, port, nad = row[4], row[13], row[12], row[14]
        cond = find_condition(arch, arpa_group, port, nad)
        if cond is None or (cond["discPct"] or 0) <= 0:
            continue
        vname = village_lookup.get(row[10])
        if not vname:
            continue
        cond_key = f"{arch}|{arpa_group}|{port}|{nad}"
        by_village.setdefault(vname, []).append({
            "id": row[10],
            "arch": arch,
            "port": port,
            "arpa": round(row[11]) if row[11] is not None else None,
            "arpaGroup": arpa_group,
            "discPct": round(cond["discPct"], 4),
            "lat": round(row[8], 6) if row[8] is not None else None,
            "lng": round(row[9], 6) if row[9] is not None else None,
            "condKey": cond_key,
        })
        mkts_by_village.setdefault(vname, {})
        if cond_key not in mkts_by_village[vname]:
            mkts_by_village[vname][cond_key] = cond["mkts"]
    return by_village, mkts_by_village


def attach_l2_data(villages, l2_by_village, l2_mkts_by_village):
    """Matching by name alone breaks when this dashboard's own village list
    has two entries sharing a name (confirmed against real data: 12 village
    names here have 2 entries each, e.g. two separate "หมู่บ้านชลลดา" ~5km
    apart) -- naively doing villages_by_name.get(r["name"]) would attach the
    SAME L2 points to both, falsely tagging the wrong one as having L2 (and
    on the map, its "L2 pin" would actually render 5km away at the real
    match's location). Where a name is ambiguous, this instead assigns the
    points to whichever same-named candidate is geographically closest to
    the L2 points' own centroid."""
    for r in villages:
        r["hasL2"] = False
        r["l2Points"] = []
        r["l2Mkts"] = {}

    by_name = {}
    for r in villages:
        by_name.setdefault(r["name"], []).append(r)

    for name, points in l2_by_village.items():
        candidates = by_name.get(name)
        if not candidates:
            continue
        if len(candidates) == 1:
            target = candidates[0]
        else:
            lats = [p["lat"] for p in points if p.get("lat") is not None]
            lngs = [p["lng"] for p in points if p.get("lng") is not None]
            if not lats:
                continue
            centroid_lat, centroid_lng = sum(lats) / len(lats), sum(lngs) / len(lngs)

            def dist(cand):
                if cand["lat"] is None or cand["lng"] is None:
                    return float("inf")
                return (cand["lat"] - centroid_lat) ** 2 + (cand["lng"] - centroid_lng) ** 2

            target = min(candidates, key=dist)
        target["hasL2"] = True
        target["l2Points"] = points
        target["l2Mkts"] = l2_mkts_by_village.get(name, {})


def build_output():
    villages = load_villages()
    buildings = load_buildings()
    add_tags_and_scores(villages, "houseAll")
    add_tags_and_scores(buildings, "units")

    l2_by_village, l2_mkts_by_village = load_l2_by_village()
    attach_l2_data(villages, l2_by_village, l2_mkts_by_village)

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
