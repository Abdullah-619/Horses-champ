"""Refresh the saved championship results.

Run by the hourly GitHub Actions job (.github/workflows/update.yml). It checks
recent and new events on arabianessence.tv, saves every Gold / Silver / Bronze
result to data/results.json and data/results.xlsx, and logs anything new or
changed in data/changes.json.

    python update_results.py                # current year (normal hourly run)
    python update_results.py 2023 2024      # also load older years
    RECHECK_DAYS=100000 python update_results.py   # re-read every saved event
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

import scraper

DATA_DIR = Path(__file__).parent / "data"
RESULTS_FILE = DATA_DIR / "results.json"
CHANGES_FILE = DATA_DIR / "changes.json"
EXCEL_FILE = DATA_DIR / "results.xlsx"
RECHECK_DAYS = int(os.environ.get("RECHECK_DAYS", 14))
REPORT_DAYS = 14
MAX_CHANGES_KEPT = 1000

COLUMNS = [
    ("Medal", "medal"), ("Result", "result"), ("Horse", "horse_name"), ("No.", "horse_number"),
    ("Championship", "championship"), ("Event", "event_name"), ("Start date", "start_date"),
    ("End date", "end_date"), ("Location", "location"), ("Horse page", "horse_url"),
    ("Source", "source"), ("Videos", "videos"),
]
MEDAL_FILL = {"platinum": "D9E4EC", "gold": "F4E3A1", "silver": "E1E4E8", "bronze": "EBCBAE"}


def _load(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def events_to_check(store: dict, years: list[int]) -> list[scraper.Event]:
    today = date.today()
    candidates: dict[str, scraper.Event] = {}
    for year in years:
        for ev in scraper.list_events(year):
            candidates[ev.event_id] = ev
    for ev in scraper.list_events():  # live and upcoming events
        candidates.setdefault(ev.event_id, ev)

    due = []
    recent = (today - timedelta(days=RECHECK_DAYS)).isoformat()
    for ev in candidates.values():
        if not ev.start_date or ev.start_date > today.isoformat():
            continue  # not started yet
        saved = store["events"].get(ev.event_id)
        if not saved or not saved["results"] or ev.start_date >= recent:
            due.append(ev)
    return sorted(due, key=lambda e: e.start_date, reverse=True)


def diff(old: list[dict], new: list[dict], checked_at: str) -> list[dict]:
    def key(r):
        return f"{r['championship']}|{r['medal']}"

    before = {key(r): r for r in old}
    changes = []
    for r in new:
        prev = before.get(key(r))
        if prev and prev["horse_url"] == r["horse_url"]:
            continue
        changes.append({
            "detected_at": checked_at,
            "type": "changed" if prev else "new",
            "previous_horse": prev["horse_name"] if prev else "",
            **{k: r[k] for k in ("medal", "horse_name", "horse_url", "championship",
                                 "event_name", "event_url", "start_date")},
        })
    return changes


def write_excel(results: list[dict], changes: list[dict]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    ws.append([c[0] for c in COLUMNS])
    for r in results:
        row = [", ".join(r[k]) if k == "videos" else r[k] for _, k in COLUMNS]
        row[0] = row[0].title()
        ws.append(row)
        ws.cell(ws.max_row, 1).fill = PatternFill("solid", fgColor=MEDAL_FILL[r["medal"]])

    cs = wb.create_sheet("Changes")
    cs.append(["Detected at (UTC)", "Type", "Medal", "Horse", "Previous horse",
               "Championship", "Event", "Start date", "Horse page"])
    for c in reversed(changes):
        cs.append([c["detected_at"], c["type"], c["medal"].title(), c["horse_name"],
                   c["previous_horse"], c["championship"], c["event_name"], c["start_date"],
                   c["horse_url"]])

    for sheet in (ws, cs):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for col in sheet.columns:
            width = max(len(str(c.value or "")) for c in col[:200])
            sheet.column_dimensions[col[0].column_letter].width = min(max(width + 2, 8), 60)
    wb.save(EXCEL_FILE)


def main(argv: list[str]) -> int:
    DATA_DIR.mkdir(exist_ok=True)
    store = _load(RESULTS_FILE, {"updated_at": "", "events": {}})
    changes = _load(CHANGES_FILE, [])
    first_run = not store["events"]

    this_year = date.today().year
    years = sorted({int(y) for y in argv if y.isdigit()} | {this_year})
    if date.today().month == 1:
        years.append(this_year - 1)

    due = events_to_check(store, years)
    print(f"Checking {len(due)} event(s) for years {years}")
    new_changes = []
    dirty = False
    for ev in due:
        try:
            event, results = scraper.event_results(ev.url)
        except Exception as exc:  # keep going if one event page fails
            print(f"  ! {ev.name}: {exc}")
            continue
        checked_at = _now()
        if not event.location:
            event.location = ev.location
            for r in results:
                r.location = ev.location
        rows = [asdict(r) for r in results]
        saved = store["events"].get(event.event_id, {"results": []})
        found = diff(saved["results"], rows, checked_at)
        # Report wins from recent events (and old events whose results only just
        # appeared), not whole old events loaded or re-read in bulk.
        recent = (date.today() - timedelta(days=REPORT_DAYS)).isoformat()
        late = event.event_id in store["events"] and not saved["results"]
        if not first_run and (ev.start_date >= recent or late):
            new_changes.extend(found)
        entry = {**asdict(event), "results": rows}
        if {k: v for k, v in saved.items() if k != "checked_at"} != entry:
            dirty = True
            store["events"][event.event_id] = {**entry, "checked_at": checked_at}
        print(f"  {event.name}: {len(rows)} results, {len(found)} new/changed")

    if not dirty and RESULTS_FILE.exists():
        # Leave the files alone so the hourly job only commits (and Render only
        # redeploys) when the results really changed.
        print("No changes; data files left as they are")
        return _report([])

    changes = (changes + new_changes)[-MAX_CHANGES_KEPT:]
    store["updated_at"] = _now()
    all_results = sorted(
        (r for e in store["events"].values() for r in e["results"]),
        key=lambda r: (r["championship"], scraper.MEDALS.index(r["medal"])),
    )
    all_results.sort(key=lambda r: (r["start_date"], r["event_id"]), reverse=True)

    RESULTS_FILE.write_text(json.dumps(store, indent=1, ensure_ascii=False, sort_keys=True),
                            encoding="utf-8")
    CHANGES_FILE.write_text(json.dumps(changes, indent=1, ensure_ascii=False), encoding="utf-8")
    write_excel(all_results, changes)

    print(f"{len(new_changes)} new or changed result(s); {len(all_results)} saved in total")
    return _report(new_changes)


def _report(new_changes: list[dict]) -> int:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### {len(new_changes)} new or changed result(s)\n\n")
            for c in new_changes:
                fh.write(f"- {c['medal'].title()}: **{c['horse_name']}**, {c['championship']} "
                         f"({c['event_name']})\n")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"changes={len(new_changes)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
