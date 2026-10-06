"""Scraper for championship results on https://www.arabianessence.tv/.

Site layout:
  /events/                              event list; POST with events-search=1 and
                                        year=YYYY returns the events of that year
  /events/<slug>/<id>/                  one event. Its "Championships" section
                                        (#championship) has one .result-box per
                                        championship with GOLD / SILVER / BRONZE rows,
                                        and the schedule has a video link per class.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.arabianessence.tv"
EVENTS_URL = f"{BASE_URL}/events/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
REQUEST_DELAY = 0.5
MEDALS = ("gold", "silver", "bronze")

EVENT_RE = re.compile(r"/events/[^/]+/(\d+)/?$")

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})


@dataclass
class Event:
    event_id: str
    name: str
    url: str
    start_date: str = ""
    end_date: str = ""
    location: str = ""


@dataclass
class Result:
    medal: str
    result: str
    horse_name: str
    horse_number: str
    horse_url: str
    championship: str
    event_id: str
    event_name: str
    event_url: str
    start_date: str
    end_date: str
    location: str
    source: str
    videos: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.event_id}|{self.championship}|{self.medal}"


def _get(url: str, data: dict | None = None) -> BeautifulSoup:
    for attempt in range(3):
        try:
            time.sleep(REQUEST_DELAY)
            if data is None:
                resp = _session.get(url, timeout=30)
            else:
                resp = _session.post(url, files={k: (None, v) for k, v in data.items()}, timeout=30)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    raise AssertionError("unreachable")


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _date(text: str, fmt: str) -> str:
    try:
        return datetime.strptime(text, fmt).date().isoformat()
    except ValueError:
        return ""


# ---------------------------------------------------------------- events list

def list_events(year: int | None = None) -> list[Event]:
    """Events from the events page, or from the site's search for one year."""
    if year:
        soup = _get(EVENTS_URL, {"name": "", "country": "", "month": "", "year": str(year),
                                 "events-search": "1"})
    else:
        soup = _get(EVENTS_URL)
    events, seen = [], set()
    for el in soup.select(".broadcast-list-element"):
        a = el.select_one("h4 a[href]")
        if not a:
            continue
        m = EVENT_RE.search(a["href"])
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        date_m = re.search(r"\d{2}-\d{2}-\d{4}", el.get_text(" "))
        loc = el.select_one(".fa-map-marker-alt")
        events.append(Event(
            event_id=m.group(1),
            name=_clean(a.get_text()),
            url=urljoin(BASE_URL, a["href"]),
            start_date=_date(date_m.group(0), "%d-%m-%Y") if date_m else "",
            location=_clean(loc.parent.get_text()) if loc else "",
        ))
    return events


# ---------------------------------------------------------------- one event

def _event_info(soup: BeautifulSoup, url: str) -> Event:
    title = _clean(soup.title.get_text()) if soup.title else ""
    name = title.split(" | ")[0] if title else url
    info = {}
    for label in ("Start", "End"):
        span = soup.find("span", string=re.compile(rf"^\s*{label}:\s*$"))
        if span and span.next_sibling:
            m = re.search(r"([A-Z][a-z]{2} \d{1,2}, \d{4})", str(span.next_sibling))
            info[label] = _date(m.group(1), "%b %d, %Y") if m else ""
    location = ""
    addr = soup.find("span", string=re.compile(r"^\s*Address:\s*$"))
    if addr:
        parts = []
        for sib in addr.next_siblings:
            if getattr(sib, "name", None) == "div":
                break
            if isinstance(sib, str) and sib.strip():
                parts.append(_clean(sib))
        location = ", ".join(parts)
    m = EVENT_RE.search(url)
    return Event(
        event_id=m.group(1) if m else "",
        name=name,
        url=url,
        start_date=info.get("Start", ""),
        end_date=info.get("End", ""),
        location=location,
    )


def _competition_videos(soup: BeautifulSoup) -> dict[str, list[str]]:
    """Map the site's competition id to its video links."""
    videos: dict[str, list[str]] = {}
    for panel in soup.select('[id^="panel-competition"]'):
        cid = panel["id"].removeprefix("panel-competition")
        if not cid.isdigit():
            continue
        links = []
        for btn in panel.select(".competition-video [data-href], .competition-video [href]"):
            link = btn.get("data-href") or btn.get("href")
            if link and link not in links:
                links.append(urljoin(BASE_URL, link))
        if links:
            videos[cid] = links
    return videos


def event_results(url: str) -> tuple[Event, list[Result]]:
    soup = _get(url)
    event = _event_info(soup, url)
    videos = _competition_videos(soup)
    results = []
    section = soup.select_one("#championship") or soup
    for box in section.select(".result-box"):
        heading = box.select_one(".header h4")
        championship = _clean(heading.get_text()) if heading else ""
        card = box.select_one('[id^="judges-card"]')
        cid = card["id"].removeprefix("judges-card") if card else ""
        for row in box.select(".content .row.res"):
            label = row.select_one(".gold, .silver, .bronze")
            link = row.select_one("a[href]")
            if not label or not link:
                continue
            medal = next(c for c in label["class"] if c in MEDALS)
            text = _clean(link.get_text())
            num, _, horse = text.partition(" - ")
            if not horse:
                num, horse = "", text
            results.append(Result(
                medal=medal,
                result=f"{medal.title()} Champion",
                horse_name=horse.strip(),
                horse_number=num.strip(),
                horse_url=urljoin(BASE_URL, link["href"]),
                championship=championship,
                event_id=event.event_id,
                event_name=event.name,
                event_url=event.url,
                start_date=event.start_date,
                end_date=event.end_date,
                location=event.location,
                source=event.url,
                videos=videos.get(cid, []),
            ))
    return event, results


if __name__ == "__main__":
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else EVENTS_URL
    if EVENT_RE.search(target):
        ev, res = event_results(target)
        print(json.dumps({"event": asdict(ev), "results": [asdict(r) for r in res]},
                         indent=2, ensure_ascii=False))
    else:
        print(json.dumps([asdict(e) for e in list_events(int(target) if target.isdigit() else None)],
                         indent=2, ensure_ascii=False))
