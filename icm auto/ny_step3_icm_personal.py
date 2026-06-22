#!/usr/bin/env python3
"""
Step 3 — Instant Checkmate (name + city/state) with verification → master_ny.xlsx

Data flow:
  file/ny.csv  →  (step 0, auto if master missing)  →  output/master_ny.xlsx  →  ICM  →  master updated

ICM does not read ny.csv on every search; it enriches the master workbook built from ny.csv.
Run step 0 first if you changed ny.csv:  python3 ny_step0_init_master.py

Flow per owner (file order):
  1. Parse first / last / middle from Owner Name
  2. Search ICM with name + Facility City + NY state
  3. Open only location-matching result cards (existing ICM logic)
  4. Verify: ICM name + location vs NY license row
  5. Save clean fields on master:
       icm_verified = correct | partial | incorrect | no_results | error
       personal_cell / personal_email only when verified correct or partial

  python3 ny_step3_icm_personal.py              # all pending; saves master after each owner
  python3 ny_step3_icm_personal.py --batch 50   # optional: only first 50 pending

Requires logged-in Chrome (second_site_automation.py / icm_name_search_automation.py).
"""
from __future__ import annotations

import argparse
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd

import icm_name_search_automation as icm
import ny_data as nd

# None = process every individual still missing icm_verified (not limited by BATCH_SIZE_PER_RUN).
MAX_ICM_SEARCHES: Optional[int] = None
PAUSE_BETWEEN = None


def _pause() -> float:
    return icm.PAUSE_BETWEEN_SEARCHES if PAUSE_BETWEEN is None else float(PAUSE_BETWEEN)


def _batch_cap(cli_batch: Optional[int] = None) -> Optional[int]:
    """ICM batch size: CLI --batch, then MAX_ICM_SEARCHES; default None = all pending."""
    if cli_batch is not None:
        return int(cli_batch)
    return MAX_ICM_SEARCHES


def ensure_master_from_ny_csv(force_rebuild: bool = False) -> Path:
    """Master is the enrichment workbook sourced from file/ny.csv."""
    mp = nd.active_master_path()
    if force_rebuild or not mp.is_file():
        print(f"[Step3] Building master from {nd.INPUT_CSV} ...")
        df = nd.load_ny_dataframe()
        master = nd.build_master_from_ny(df)
        saved = nd.save_master(master)
        print(f"[Step3] Master saved: {saved} ({len(master)} rows from ny.csv)")
        return Path(saved)
    return mp


def _split_pipe(val: Optional[str]) -> list[str]:
    if not val:
        return []
    return [x.strip() for x in str(val).split("|") if x.strip()]


def _norm_name_tokens(name: str) -> set[str]:
    s = re.sub(r"[^A-Za-z0-9\s]", " ", (name or "").upper())
    return {t for t in s.split() if len(t) >= 2}


def name_matches_owner(
    owner_raw: str,
    first: str,
    last: str,
    report_name: str,
) -> bool:
    """ICM report name should share last name + first name (or strong token overlap)."""
    if not report_name:
        return False
    r_up = report_name.upper()
    last_u = (last or "").upper().strip()
    first_u = (first or "").upper().strip()
    if last_u and last_u in r_up:
        if not first_u:
            return True
        if first_u in r_up:
            return True
        if len(first_u) >= 1 and first_u[0] in r_up.split():
            return True
    owner_t = _norm_name_tokens(owner_raw)
    report_t = _norm_name_tokens(report_name)
    if last_u and last_u in report_t:
        overlap = owner_t & report_t
        return len(overlap) >= 2
    return len(owner_t & report_t) >= 2


def verify_icm_record(
    row: dict[str, Any],
    record: icm.NameSearchResultRow,
) -> dict[str, str]:
    """
    Compare NY license row vs ICM scrape.
    Returns dict with icm_verified, icm_name_match, icm_location_match, icm_report_name, etc.
    """
    owner = row.get("owner_name_raw") or ""
    first = row.get("search_first_name") or ""
    last = row.get("search_last_name") or ""
    report = (record.report_name or record.result_card_name or "").strip()
    locs = (record.locations or record.result_card_locations or "").strip()
    loc_short = " | ".join(_split_pipe(locs)[:3])

    name_ok = name_matches_owner(owner, first, last, report)
    loc_ok = bool(record.location_match)
    if record.status == "no_location_match":
        loc_ok = False
    elif record.location_match is False:
        loc_ok = False
    elif record.location_match is True:
        loc_ok = True

    has_phone = bool((record.phone_numbers or "").strip())
    has_email = bool((record.emails or "").strip())
    status = (record.status or "").strip()

    if status in ("no_results",):
        verified = "no_results"
    elif status in ("timeout", "error", "extract_error"):
        verified = "error"
    elif name_ok and loc_ok and (has_phone or has_email) and status == "ok":
        verified = "correct"
    elif name_ok and loc_ok and (has_phone or has_email):
        verified = "partial"
    elif not name_ok and not loc_ok:
        verified = "incorrect"
    elif name_ok and not loc_ok:
        verified = "incorrect"
    elif loc_ok and not name_ok:
        verified = "incorrect"
    elif status == "no_location_match":
        verified = "incorrect"
    else:
        verified = "incorrect" if status else "no_results"

    return {
        "icm_verified": verified,
        "icm_name_match": "yes" if name_ok else "no",
        "icm_location_match": "yes" if loc_ok else "no",
        "icm_report_name": report,
        "icm_locations_found": loc_short,
        "icm_status": status or verified,
    }


def _pick_verified_contact(
    records: List[icm.NameSearchResultRow],
    row: dict[str, Any],
) -> tuple[dict[str, str], str, str]:
    """Best verified record; phones/emails only if correct or partial."""
    if not records:
        return {
            "icm_verified": "no_results",
            "icm_name_match": "no",
            "icm_location_match": "no",
            "icm_report_name": "",
            "icm_locations_found": "",
            "icm_status": "no_results",
        }, "", ""

    scored: list[tuple[int, icm.NameSearchResultRow, dict[str, str]]] = []
    for rec in records:
        v = verify_icm_record(row, rec)
        rank = {
            "correct": 4,
            "partial": 3,
            "incorrect": 1,
            "no_results": 0,
            "error": 0,
        }.get(v["icm_verified"], 0)
        if v["icm_name_match"] == "yes":
            rank += 2
        if v["icm_location_match"] == "yes":
            rank += 2
        scored.append((rank, rec, v))

    scored.sort(key=lambda x: x[0], reverse=True)
    _, best_rec, best_v = scored[0]

    cell, email = "", ""
    if best_v["icm_verified"] in ("correct", "partial"):
        phones = _split_pipe(best_rec.phone_numbers)
        emails = _split_pipe(best_rec.emails)
        cell = phones[0] if phones else ""
        email = emails[0].lower() if emails else ""

    return best_v, cell, email


def master_rows_to_icm_input(master: pd.DataFrame) -> list[dict[str, Any]]:
    cmap = nd._norm_col_map(master)
    c_owner = cmap.get("owner name") or cmap.get("owner name ")
    c_city = cmap.get("facility city")
    c_state = cmap.get("facility state")
    c_street = cmap.get("facility street")
    c_num = cmap.get("facility #") or cmap.get("facility number")
    c_fname = cmap.get("facility name")
    c_own_ov = cmap.get("owner name overflow")

    if not c_owner:
        raise ValueError(f"Owner column missing. Columns: {list(master.columns)}")

    rows: list[dict[str, Any]] = []
    for i, ser in master.iterrows():
        owner = nd._cell_str(ser.get(c_owner))
        if not owner:
            continue
        own_ov = nd._cell_str(ser.get(c_own_ov)) if c_own_ov else ""
        otype = nd.classify_owner(owner, own_ov)
        fc = nd._cell_str(ser.get(c_city)) if c_city else ""
        fs = nd._cell_str(ser.get(c_state)) if c_state else ""
        fst = nd._cell_str(ser.get(c_street)) if c_street else ""

        prid = ser.get("pipeline_row_id")
        try:
            row_index = int(float(str(prid).strip())) if nd._cell_str(prid) else int(i) + 2
        except ValueError:
            row_index = int(i) + 2

        first, last, mid = "", "", ""
        if otype == "individual":
            first, last, mid = icm.parse_owner_name(owner)

        rows.append(
            {
                "input_row_index": row_index,
                "facility_number": nd._cell_str(ser.get(c_num)) if c_num else None,
                "facility_name": nd._cell_str(ser.get(c_fname)) if c_fname else None,
                "facility_street": fst or None,
                "facility_city": fc or None,
                "facility_state": fs or None,
                "owner_name_raw": owner,
                "owner_type": otype,
                "search_first_name": first or None,
                "search_last_name": last or None,
                "search_middle": mid or None,
                "search_city_typed": icm.city_for_search_form(fc, fst) if otype == "individual" else None,
                "search_state_typed": icm.state_for_dropdown(fs) if otype == "individual" else None,
            }
        )
    return rows


def _pipeline_row_id_from_series(ser: pd.Series, i: int) -> int:
    prid_raw = ser.get("pipeline_row_id")
    try:
        return int(float(str(prid_raw).strip())) if nd._cell_str(prid_raw) else int(i) + 2
    except ValueError:
        return int(i) + 2


def _clone_icm_results_for_row(
    scraped: List[icm.NameSearchResultRow],
    row: dict[str, Any],
) -> List[icm.NameSearchResultRow]:
    clones: List[icm.NameSearchResultRow] = []
    for s in scraped:
        clone = icm.NameSearchResultRow(**asdict(s))
        clone.input_row_index = row.get("input_row_index", 0)
        clone.facility_number = row.get("facility_number")
        clone.facility_name = row.get("facility_name")
        clone.facility_street = row.get("facility_street")
        clone.facility_city = row.get("facility_city")
        clone.facility_state = row.get("facility_state")
        clone.owner_name_raw = row.get("owner_name_raw")
        clones.append(clone)
    return clones


def apply_icm_result_for_owner(
    master: pd.DataFrame,
    row: dict[str, Any],
    recs: List[icm.NameSearchResultRow],
) -> pd.DataFrame:
    """Apply one owner's ICM results to master (single row)."""
    master = nd.ensure_enrichment_columns(master)
    prid = int(row.get("input_row_index", 0))

    for i, ser in master.iterrows():
        if _pipeline_row_id_from_series(ser, i) != prid:
            continue
        if nd._cell_str(ser.get("icm_verified")):
            return master

        if row.get("owner_type") != "individual":
            master.at[i, "icm_verified"] = "skipped_corporate"
            master.at[i, "icm_status"] = "skipped_corporate"
            return master

        verified, cell, email = _pick_verified_contact(recs, row)
        for k, v in verified.items():
            master.at[i, k] = v
        if cell:
            master.at[i, "personal_cell"] = cell
        if email:
            master.at[i, "personal_email"] = email

        owner = row.get("owner_name_raw", "")
        rep = verified.get("icm_report_name", "")
        flag = verified.get("icm_verified", "")
        print(
            f"  excel~{prid} | NY: {owner!r} | ICM: {rep!r} | "
            f"name={verified.get('icm_name_match')} loc={verified.get('icm_location_match')} "
            f"→ {flag.upper()}"
        )
        break
    return master


def _append_audit_rows(audit_path: Path, rows: List[icm.NameSearchResultRow]) -> None:
    if not rows:
        return
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    new_df = icm._flatten_rows_for_excel(rows)
    if audit_path.is_file():
        try:
            old_df = pd.read_excel(audit_path, dtype=str, keep_default_na=False)
            out_df = pd.concat([old_df, new_df], ignore_index=True)
        except Exception:
            out_df = new_df
    else:
        out_df = new_df
    try:
        out_df.to_excel(audit_path, index=False)
    except Exception as e:
        print(f"[Step3] Audit save skipped: {e}")


def apply_icm_results_to_master(
    master: pd.DataFrame,
    icm_results: List[icm.NameSearchResultRow],
    input_rows: list[dict[str, Any]],
) -> pd.DataFrame:
    master = nd.ensure_enrichment_columns(master)
    by_row: dict[int, list[icm.NameSearchResultRow]] = {}
    for r in icm_results:
        by_row.setdefault(int(r.input_row_index or 0), []).append(r)

    row_by_prid = {int(r["input_row_index"]): r for r in input_rows}
    updated = 0

    for i, ser in master.iterrows():
        prid_raw = ser.get("pipeline_row_id")
        try:
            prid = int(float(str(prid_raw).strip())) if nd._cell_str(prid_raw) else int(i) + 2
        except ValueError:
            prid = int(i) + 2

        if nd._cell_str(ser.get("icm_verified")):
            continue

        src = row_by_prid.get(prid)
        if not src:
            continue

        if src.get("owner_type") != "individual":
            master.at[i, "icm_verified"] = "skipped_corporate"
            master.at[i, "icm_status"] = "skipped_corporate"
            continue

        recs = by_row.get(prid, [])
        verified, cell, email = _pick_verified_contact(recs, src)

        for k, v in verified.items():
            master.at[i, k] = v

        if cell:
            master.at[i, "personal_cell"] = cell
        if email:
            master.at[i, "personal_email"] = email

        owner = src.get("owner_name_raw", "")
        rep = verified.get("icm_report_name", "")
        flag = verified.get("icm_verified", "")
        print(
            f"  excel~{prid} | NY: {owner!r} | ICM: {rep!r} | "
            f"name={verified.get('icm_name_match')} loc={verified.get('icm_location_match')} "
            f"→ {flag.upper()}"
        )
        updated += 1

    print(f"[Step3] Updated {updated} master row(s) with ICM verification.")
    return master


def run_step3(
    *,
    force_step0: bool = False,
    batch_limit: Optional[int] = None,
) -> str:
    ensure_master_from_ny_csv(force_rebuild=force_step0)
    master = nd.load_master()
    todo_df = nd.rows_needing_icm(master)
    all_icm_rows = master_rows_to_icm_input(master)

    want_prids: list[int] = []
    for _, ser in todo_df.iterrows():
        pr = ser.get("pipeline_row_id")
        try:
            want_prids.append(int(float(str(pr).strip())))
        except (ValueError, TypeError):
            pass

    pending_rows = [
        r
        for r in all_icm_rows
        if r.get("owner_type") == "individual" and r.get("input_row_index") in want_prids
    ]

    total_missing = len(pending_rows)
    cap = _batch_cap(batch_limit)
    input_rows = pending_rows[: int(cap)] if cap is not None else list(pending_rows)

    ind_total = sum(1 for r in all_icm_rows if r.get("owner_type") == "individual")
    done_icm = ind_total - total_missing

    print(f"[Step3] Source: {nd.INPUT_CSV} → master {nd.active_master_path()}")
    print(f"[Step3] Individuals: {ind_total} | ICM already done: {done_icm} | pending: {total_missing}")
    if not input_rows:
        print("[Step3] Nothing to search.")
        return str(nd.active_master_path())

    prids = [str(r.get("input_row_index")) for r in input_rows]
    if cap is not None:
        batch_note = f"batch limit {cap}"
    else:
        batch_note = "all pending (no batch limit)"
    if len(prids) <= 15:
        prid_note = ", ".join(prids)
    else:
        prid_note = f"{prids[0]} … {prids[-1]} ({len(prids)} rows)"
    print(f"[Step3] This run: {len(input_rows)} owner(s) ({batch_note}) — pipeline rows: {prid_note}")

    driver = icm.icm.build_driver(headless=icm.HEADLESS)
    audit_path = nd._PROJECT_ROOT / "output" / "icm_step3_audit.xlsx"
    search_cache: dict[str, List[icm.NameSearchResultRow]] = {}
    saved = str(nd.active_master_path())

    try:
        icm.icm.ensure_icm_session(driver)
        for n, row in enumerate(input_rows, start=1):
            k = icm._search_dedupe_key(row)
            if k not in search_cache:
                print(
                    f"[Step3] [{n}/{len(input_rows)}] Search ICM: "
                    f"{row.get('search_first_name')} {row.get('search_last_name')} | "
                    f"city={row.get('search_city_typed')} | state={row.get('search_state_typed')}"
                )
                search_cache[k] = icm.process_one_row(driver, row)
                time.sleep(_pause())
            else:
                print(
                    f"[Step3] [{n}/{len(input_rows)}] Reuse cached search: "
                    f"{row.get('owner_name_raw')}"
                )

            clones = _clone_icm_results_for_row(search_cache[k], row)
            master = apply_icm_result_for_owner(master, row, clones)
            saved = nd.save_master(master)
            _append_audit_rows(audit_path, clones)
            print(f"[Step3] Saved master + audit after pipeline row {row.get('input_row_index')}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    ok = (master["icm_verified"].astype(str).isin(["correct", "partial"])).sum()
    bad = (master["icm_verified"].astype(str) == "incorrect").sum()
    print(f"[Step3] Verified correct/partial: {ok} | incorrect: {bad}")
    print(f"[Step3] Master: {saved}")
    print(f"[Step3] Audit: {audit_path}")
    return saved


def main() -> None:
    p = argparse.ArgumentParser(
        description="ICM personal contact search — enriches master built from ny.csv",
    )
    p.add_argument(
        "--step0",
        action="store_true",
        help="Rebuild master from ny.csv before ICM (use after ny.csv changes)",
    )
    p.add_argument(
        "--batch",
        type=int,
        default=None,
        metavar="N",
        help="Optional: only process first N pending individuals (default: all)",
    )
    args = p.parse_args()

    print("=" * 60)
    print("NY pipeline — Step 3: ICM + verification")
    print("=" * 60)
    print("Input data:", nd.INPUT_CSV)
    print("Master:", nd.active_master_path())
    print("ICM search: first + last name, Facility City, NY state")
    print("Verify: name + location must match before saving personal phone/email")
    if args.batch is None and MAX_ICM_SEARCHES is None:
        print("Batch: all pending individuals (no 50-row cap)")
    print("=" * 60)
    run_step3(force_step0=args.step0, batch_limit=args.batch)


if __name__ == "__main__":
    main()
