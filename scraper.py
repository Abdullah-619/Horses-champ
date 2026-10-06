"""Scraper for championship results on https://www.arabianessence.tv/.

Site layout (as observed):
  /events/                                   list of events (live, upcoming, past)
  /events/<event-slug>/<event-id>/           one event: dates, location, classes
  /events/<event-slug>/<event-id>/<class-slug>/<class-id>/   one class/championship
  /horses/<horse-slug>/<horse-id>/           one horse

The parsing below is deliberately tolerant: it looks for links and text patterns
rather than exact CSS classes, so small layout changes don't break it.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.arabianessence.tv"
EVENTS_URL = f"{BASE_URL}/events/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", 6 * 3600))
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY_SECONDS", 0.3))

MEDALS = ("gold", "silver", "bronze")

EVENT_RE = re.compile(r"^/events/([^/]+)/(\d+)/?$")
CLASS_RE = re.compile(r"^/events/([^/]+)/(\d+)/([^/]+)/(\d+)/?$")
HORSE_RE = re.compile(r"/horses/([^/]+)/(\d+)/?$")
DATE_RE = re.compile(r"\b(\d{2})[-/.](\d{2})[-/.](\d{4})\b")
VIDEO_HOST_RE = re.compile(r"(youtube\.com|youtu\.be|vimeo\.com|\.mp4|\.m3u8|/videos?/)", re.I)


@dataclass
class Event:
    name: str
    url: str
    event_id: str
    start_date: str = ""
    end_date: str = ""
    location: str = ""


@dataclass
class Result:
    medal: str
    result: str
    horse_name: str
    horse_url: str
    championship: str
    championship_url: str
    event_name: str
    event_url: str
    start_date: str
    end_date: str
    location: str
    source: str
    videos: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- HTTP + cache

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _cached(key: str, producer):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    value = producer()
    with _lock:
        _cache[key] = (now, value)
    return value


def fetch(url: str) -> BeautifulSoup:
    def get():
        time.sleep(REQUEST_DELAY)
        resp = _session.get(url, timeout=25)
        resp.raise_for_status()
        return resp.text

    return BeautifulSoup(_cached("html:" + url, get), "html.parser")


def _path(href: str) -> str:
    return urlparse(urljoin(BASE_URL, href)).path


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _to_iso(d: str) -> str:
    m = DATE_RE.search(d or "")
    if not m:
        return ""
    day, month, year = m.groups()
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return ""


# ---------------------------------------------------------------- events

def list_events() -> list[Event]:
    """All events linked from the events page, newest first, without duplicates."""

    def produce():
        soup = fetch(EVENTS_URL)
        seen, events = set(), []
        for a in soup.find_all("a", href=True):
            m = EVENT_RE.match(_path(a["href"]))
            if not m or m.group(2) in seen:
                continue
            name = _clean(a.get_text(" ")) or m.group(1).replace("-", " ").title()
            # Dates are usually printed next to the link inside the same card/row.
            container = a.find_parent(["tr", "li", "article", "div"]) or a
            dates = [_to_iso("-".join(t)) for t in DATE_RE.findall(container.get_text(" "))]
            seen.add(m.group(2))
            events.append(Event(
                name=name,
                url=urljoin(BASE_URL, _path(a["href"])),
                event_id=m.group(2),
                start_date=dates[0] if dates else "",
                end_date=dates[-1] if dates else "",
            ))
        return events

    return _cached("events", produce)


def event_details(event_url: str) -> tuple[Event, list[tuple[str, str]]]:
    """Return the event's header info plus (class name, class url) pairs."""
    soup = fetch(event_url)
    m = EVENT_RE.match(_path(event_url))
    title = soup.find(["h1", "h2"])
    text = soup.get_text(" ")
    dates = [_to_iso("-".join(t)) for t in DATE_RE.findall(text)]
    loc = ""
    loc_m = re.search(r"([A-Za-z.'/-]+(?:,? [A-Za-z.'/-]+){0,3} \([A-Z][A-Za-z .'-]+\))", text)
    if loc_m:
        loc = _clean(loc_m.group(1))
    event = Event(
        name=_clean(title.get_text(" ")) if title else (m.group(1) if m else event_url),
        url=event_url,
        event_id=m.group(2) if m else "",
        start_date=dates[0] if dates else "",
        end_date=dates[1] if len(dates) > 1 else (dates[0] if dates else ""),
        location=loc,
    )
    classes, seen = [], set()
    for a in soup.find_all("a", href=True):
        cm = CLASS_RE.match(_path(a["href"]))
        if cm and cm.group(4) not in seen and cm.group(2) == event.event_id:
            seen.add(cm.group(4))
            classes.append((_clean(a.get_text(" ")) or cm.group(3).replace("-", " ").title(),
                            urljoin(BASE_URL, _path(a["href"]))))
    return event, classes


def _is_championship(name: str) -> bool:
    return "champion" in name.lower()


# ---------------------------------------------------------------- results

def _medal_of(text: str) -> str | None:
    t = text.lower()
    for medal in MEDALS:
        if medal in t:
            return medal
    # Some shows print placings only: 1st = gold, 2nd = silver, 3rd = bronze.
    m = re.match(r"^\s*(1|2|3)(st|nd|rd)?\b", t)
    if m:
        return MEDALS[int(m.group(1)) - 1]
    return None


def _videos(node) -> list[str]:
    urls = set()
    for tag in node.find_all(["a", "iframe", "source", "video"]):
        src = tag.get("href") or tag.get("src") or ""
        if src and VIDEO_HOST_RE.search(src):
            urls.add(urljoin(BASE_URL, src))
    return sorted(urls)


def class_results(event: Event, class_name: str, class_url: str) -> list[Result]:
    soup = fetch(class_url)
    heading = soup.find(["h1", "h2"])
    championship = class_name or (_clean(heading.get_text(" ")) if heading else "")
    page_videos = _videos(soup)
    results = []
    seen_horses = set()
    for a in soup.find_all("a", href=True):
        if not HORSE_RE.search(_path(a["href"])):
            continue
        row = a.find_parent(["tr", "li", "article"]) or a.find_parent("div") or a
        row_text = _clean(row.get_text(" "))
        medal = _medal_of(row_text)
        horse_url = urljoin(BASE_URL, _path(a["href"]))
        if not medal or horse_url in seen_horses:
            continue
        seen_horses.add(horse_url)
        label = re.search(r"((gold|silver|bronze)[\w ]*?champion\w*)", row_text, re.I)
        results.append(Result(
            medal=medal,
            result=_clean(label.group(1)).title() if label else f"{medal.title()} Champion",
            horse_name=_clean(a.get_text(" ")),
            horse_url=horse_url,
            championship=championship,
            championship_url=class_url,
            event_name=event.name,
            event_url=event.url,
            start_date=event.start_date,
            end_date=event.end_date,
            location=event.location,
            source=class_url,
            videos=_videos(row) or page_videos,
        ))
    return results


def search(medal: str, event_url: str | None = None, max_events: int = 5) -> list[dict]:
    """Results with the given medal, for one event or the latest finished events."""
    medal = medal.lower()
    if medal not in MEDALS:
        raise ValueError(f"medal must be one of {MEDALS}")

    def produce():
        if event_url:
            targets = [event_url]
        else:
            today = datetime.utcnow().date().isoformat()
            past = [e for e in list_events() if e.start_date and e.start_date <= today]
            targets = [e.url for e in past[:max_events]]
        out = []
        for url in targets:
            try:
                event, classes = event_details(url)
            except requests.RequestException:
                continue
            for name, curl in classes:
                if not _is_championship(name):
                    continue
                try:
                    out.extend(asdict(r) for r in class_results(event, name, curl))
                except requests.RequestException:
                    continue
        return out

    all_results = _cached(f"results:{event_url}:{max_events}", produce)
    return [r for r in all_results if r["medal"] == medal]


if __name__ == "__main__":
    import sys

    medal = sys.argv[1] if len(sys.argv) > 1 else "gold"
    url = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(search(medal, url), indent=2, ensure_ascii=False))
