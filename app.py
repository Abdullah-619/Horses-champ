import json
import os
import re
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

DATA_DIR = Path(__file__).parent / "data"
MEDALS = ("platinum", "gold", "silver", "bronze")

app = Flask(__name__)


def load_store():
    path = DATA_DIR / "results.json"
    if not path.exists():
        return {"updated_at": "", "events": {}}
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/events")
def api_events():
    store = load_store()
    events = [
        {k: e[k] for k in ("event_id", "name", "url", "start_date", "end_date", "location")}
        for e in store["events"].values() if e["results"]
    ]
    events.sort(key=lambda e: e["start_date"], reverse=True)
    return jsonify(updated_at=store["updated_at"], events=events)


def _norm(text: str) -> str:
    """Lower case without spaces or punctuation: "Seraj E.H" and "seraj eh" match."""
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def _profile(horse_url: str) -> str:
    """The horse's own page (all its shows) from an event's horse link."""
    m = re.search(r"/horses/[^/]+/\d+/?$", horse_url)
    return f"https://www.arabianessence.tv{m.group(0)}" if m else horse_url


@app.route("/api/results", methods=["GET", "POST"])
def api_results():
    medal = request.values.get("medal", "all").lower()
    if medal != "all" and medal not in MEDALS:
        return jsonify(error=f"medal must be all or one of {', '.join(MEDALS)}"), 400
    event_id = request.values.get("event", "")
    # Several names can be searched at once, separated by commas or new lines.
    raw = [t.strip() for t in re.split(r"[,;\n]+", request.values.get("q", "")) if _norm(t)]
    raw = list({_norm(t): t for t in reversed(raw)}.values())[::-1]  # drop repeats, keep order
    terms = [_norm(t) for t in raw]
    # A name search shows every win; browsing everything shows the latest ones.
    limit = 20000 if terms else 1000
    store = load_store()
    events = store["events"].values()
    if event_id:
        events = [e for e in events if e["event_id"] == event_id]
    rows = [r for e in events for r in e["results"]]

    not_found = []
    if terms:
        text = {id(r): (_norm(r["horse_name"]), _norm(r["championship"]) + "|" + _norm(r["event_name"]))
                for r in rows}

        def matches(r, t):
            horse, other = text[id(r)]
            return t in horse or t in other
        not_found = [orig for orig, t in zip(raw, terms) if not any(matches(r, t) for r in rows)]
        rows = [r for r in rows if any(matches(r, t) for t in terms)]

    counts = {m: 0 for m in MEDALS}
    horses: dict[str, dict] = {}
    for r in rows:
        counts[r["medal"]] += 1
        if terms and any(t in text[id(r)][0] for t in terms):
            h = horses.setdefault(_norm(r["horse_name"]), {
                "horse_name": r["horse_name"], "horse_url": _profile(r["horse_url"]),
                **{m: 0 for m in MEDALS}})
            h[r["medal"]] += 1

    if medal != "all":
        rows = [r for r in rows if r["medal"] == medal]
    rows.sort(key=lambda r: MEDALS.index(r["medal"]))
    rows.sort(key=lambda r: (r["start_date"], r["event_id"]), reverse=True)
    return jsonify(updated_at=store["updated_at"], total=len(rows), results=rows[:limit],
                   counts=counts, horses=sorted(horses.values(), key=lambda h: h["horse_name"]),
                   not_found=not_found)


@app.get("/api/changes")
def api_changes():
    path = DATA_DIR / "changes.json"
    changes = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return jsonify(list(reversed(changes))[:200])


@app.get("/download/results.xlsx")
def download_excel():
    path = DATA_DIR / "results.xlsx"
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name="championship-results.xlsx")


@app.get("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
