"""Check the saved results against each horse's own page on arabianessence.tv.

Every horse page (/horses/<slug>/<id>/) lists that horse's show record, including
lines such as "GOLD CH. at <event> - <class>". This script compares those lines
with data/results.json and writes what doesn't match.

    python verify_horses.py 0 8     # check shard 0 of 8 (run by verify.yml)
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import scraper

DATA = Path(__file__).parent / "data" / "results.json"
HORSE_ID_RE = re.compile(r"/horses/([^/]+)/(\d+)/?$")
EVENT_ID_RE = re.compile(r"/events/[^/]+/(\d+)/")
LABEL_RE = re.compile(r"^(PLATINUM|GOLD|SILVER|BRONZE)\b.*\bCH", re.I)


def saved_by_horse(store: dict) -> dict[str, dict]:
    horses: dict[str, dict] = {}
    for e in store["events"].values():
        for r in e["results"]:
            m = HORSE_ID_RE.search(r["horse_url"])
            if not m:
                continue
            h = horses.setdefault(m.group(2), {"slug": m.group(1), "name": r["horse_name"],
                                               "wins": Counter()})
            h["wins"][(r["event_id"], r["medal"])] += 1
    return horses


def profile_wins(slug: str, horse_id: str) -> list[dict]:
    soup = scraper._get(f"{scraper.BASE_URL}/horses/{slug}/{horse_id}/")
    wins = []
    for entry in soup.select("#results .entry"):
        label = entry.select_one("strong")
        m = LABEL_RE.match(scraper._clean(label.get_text())) if label else None
        links = entry.select("a[href]")
        if not m or not links:
            continue
        ev = EVENT_ID_RE.search(links[0]["href"])
        wins.append({
            "medal": m.group(1).lower(),
            "event_id": ev.group(1) if ev else "",
            "event_name": scraper._clean(links[0].get_text()),
            "championship": scraper._clean(links[-1].get_text()) if len(links) > 1 else "",
            "url": links[-1]["href"],
        })
    return wins


def main(shard: int, shards: int) -> None:
    store = json.loads(DATA.read_text(encoding="utf-8"))
    horses = saved_by_horse(store)
    ids = sorted(horses)[shard::shards]
    report = {"checked": 0, "ok": 0, "failed": [], "missing": [], "extra": []}
    for i, horse_id in enumerate(ids):
        h = horses[horse_id]
        try:
            wins = profile_wins(h["slug"], horse_id)
        except Exception as exc:  # keep going; list the horse as not checked
            report["failed"].append({"horse_id": horse_id, "name": h["name"], "error": str(exc)})
            continue
        report["checked"] += 1
        site = Counter((w["event_id"], w["medal"]) for w in wins)
        missing = site - h["wins"]
        extra = h["wins"] - site
        if not missing and not extra:
            report["ok"] += 1
        for key in missing:
            for w in wins:
                if (w["event_id"], w["medal"]) == key:
                    report["missing"].append({
                        "horse_id": horse_id, "horse": h["name"], **w,
                        "event_saved": w["event_id"] in store["events"]})
                    break
        for (event_id, medal), n in extra.items():
            report["extra"].append({"horse_id": horse_id, "horse": h["name"], "event_id": event_id,
                                    "medal": medal, "count": n})
        if i % 200 == 0:
            print(f"{i}/{len(ids)} checked", flush=True)
    out = Path("report") / f"shard-{shard}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"shard {shard}: {report['checked']} horses checked, {report['ok']} match, "
          f"{len(report['missing'])} missing, {len(report['extra'])} extra, "
          f"{len(report['failed'])} failed")


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]))
