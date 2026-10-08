"""Scraper for championship results on https://www.arabianessence.tv/.

Site layout:
  /events/                              event list; POST with events-search=1 and
                                        year=YYYY returns the events of that year
  /events/<slug>/<id>/                  one event. Its "Championships" section
                                        (#championship) has one .result-box per
                                        championship with GOLD / SILVER / BRONZE rows,
                                        and the schedule has a video link per class.
                                        Its "Awards" section (#awards-wrap) lists
                                        titles such as "Mares Platinum Championship".
  /events/<slug>/<id>/awards/<award-slug>/<award-id>/
                                        one award, opened, with its placings.
  /events/<slug>/<id>/<class-slug>/<class-id>/
                                        one class, opened. Some events leave the
                                        Championships section empty; their GOLD /
                                        SILVER / BRONZE rows are only on these pages.
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
MEDALS = ("platinum", "gold", "silver", "bronze")

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


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _platinum_results(soup: BeautifulSoup, event: Event) -> list[Result]:
    """Winners of the event's Platinum awards (only some shows have them)."""
    results = []
    for head in soup.select("#awards-wrap .awards-rows[data-award]"):
        award_id = head["data-award"]
        name_el = head.select_one(".feature-name")
        name = _clean(name_el.get_text()) if name_el else ""
        if not award_id.isdigit() or "platinum" not in name.lower():
            continue
        videos = [urljoin(BASE_URL, b["data-href"]) for b in head.select("[data-href]")]
        page = _get(f"{event.url.rstrip('/')}/awards/{_slug(name)}/{award_id}/")
        panel = page.select_one(f"#award{award_id}")
        if not panel:
            continue
        for row in panel.select(".results-rows"):
            place = row.select_one("h4")
            link = row.select_one(".horse-name a[href]")
            if not place or not link or not _clean(place.get_text()).startswith("1"):
                continue
            num = row.select_one(".horse-name strong")
            results.append(Result(
                medal="platinum",
                result="Platinum Champion",
                horse_name=_clean(link.get_text()),
                horse_number=_clean(num.get_text()) if num else "",
                horse_url=urljoin(BASE_URL, link["href"]),
                championship=name,
                event_id=event.event_id,
                event_name=event.name,
                event_url=event.url,
                start_date=event.start_date,
                end_date=event.end_date,
                location=event.location,
                source=f"{event.url.rstrip('/')}/awards/{_slug(name)}/{award_id}/",
                videos=videos,
            ))
    return results


def _championship_classes(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """(class id, name) of each championship class in the event's programme."""
    classes = []
    for body in soup.select(".competition-championship"):
        panel = body.find_parent(id=re.compile(r"^panel-competition\d+$"))
        name_el = panel.select_one(".feature-name") if panel else None
        if name_el and _clean(name_el.get_text()):
            classes.append((panel["id"].removeprefix("panel-competition"), _clean(name_el.get_text())))
    return classes


def class_results(event: Event, cid: str, name: str, source: str,
                  videos: list[str]) -> list[Result]:
    """GOLD / SILVER / BRONZE rows on one class's own page."""
    page = _get(source)
    opened = page.select_one(f"#competition{cid}")
    if not opened:
        return []
    results = []
    for row in opened.select(".results-rows"):
        label = row.select_one("h4")
        link = row.select_one(".horse-name a[href]")
        medal = _clean(label.get_text()).lower() if label else ""
        if medal not in MEDALS or not link:
            continue
        num = row.select_one(".horse-name strong")
        results.append(Result(
            medal=medal,
            result=f"{medal.title()} Champion",
            horse_name=_clean(link.get_text()),
            horse_number=_clean(num.get_text()) if num else "",
            horse_url=urljoin(BASE_URL, link["href"]),
            championship=name,
            event_id=event.event_id,
            event_name=event.name,
            event_url=event.url,
            start_date=event.start_date,
            end_date=event.end_date,
            location=event.location,
            source=source,
            videos=videos,
        ))
    return results


def _class_page_results(soup: BeautifulSoup, event: Event, covered: set[str],
                        extra_classes: list[str]) -> list[Result]:
    """Championship classes missing from the Championships section, plus any
    other classes known to award medals (extra_classes), read from each class's
    own page."""
    videos = _competition_videos(soup)
    names = {}
    for panel in soup.select('[id^="panel-competition"]'):
        name_el = panel.select_one(".feature-name")
        if name_el:
            names[panel["id"].removeprefix("panel-competition")] = _clean(name_el.get_text())
    todo = {cid: (name, f"{event.url.rstrip('/')}/{_slug(name)}/{cid}/")
            for cid, name in _championship_classes(soup) if cid not in covered}
    for url in extra_classes:  # read even if covered: the summary box may be incomplete
        url = url.split("#")[0]
        m = re.search(r"/(\d+)/?$", url)
        if m and names.get(m.group(1)):
            todo.setdefault(m.group(1), (names[m.group(1)], urljoin(BASE_URL, url)))
    results = []
    for cid, (name, source) in todo.items():
        results += class_results(event, cid, name, source, videos.get(cid, []))
    return results


def event_results(url: str, extra_classes: list[str] = ()) -> tuple[Event, list[Result]]:
    soup = _get(url)
    event = _event_info(soup, url)
    videos = _competition_videos(soup)
    results = []
    covered: set[str] = set()
    class_ids = {_slug(name): cid for cid, name in _championship_classes(soup)}
    section = soup.select_one("#championship") or soup
    for box in section.select(".result-box"):
        heading = box.select_one(".header h4")
        championship = _clean(heading.get_text()) if heading else ""
        card = box.select_one('[id^="judges-card"]')
        cid = card["id"].removeprefix("judges-card") if card else ""
        cid = cid or class_ids.get(_slug(championship), "")  # some boxes have no judges card
        before = len(results)
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
        if len(results) > before:  # an empty box doesn't count; read the class page instead
            covered.add(cid)
    found = (_platinum_results(soup, event) + results
             + _class_page_results(soup, event, covered, list(extra_classes)))
    unique, seen = [], set()
    for r in found:
        key = (_slug(r.championship), r.medal, r.horse_url)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return event, unique


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
