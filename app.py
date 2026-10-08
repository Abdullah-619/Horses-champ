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


@app.get("/api/results")
def api_results():
    medal = request.args.get("medal", "gold").lower()
    if medal != "all" and medal not in MEDALS:
        return jsonify(error=f"medal must be all or one of {', '.join(MEDALS)}"), 400
    event_id = request.args.get("event", "")
    # Several names can be searched at once, separated by commas or new lines.
    terms = list(dict.fromkeys(t.strip().lower() for t in re.split(r"[,;\n]+", request.args.get("q", ""))
                               if t.strip()))
    store = load_store()
    events = store["events"].values()
    if event_id:
        events = [e for e in events if e["event_id"] == event_id]
    rows = [r for e in events for r in e["results"] if medal == "all" or r["medal"] == medal]
    not_found = []
    if terms:
        def matches(r, t):
            return t in r["horse_name"].lower() or t in r["championship"].lower() \
                or t in r["event_name"].lower()
        not_found = [t for t in terms if not any(matches(r, t) for r in rows)]
        rows = [r for r in rows if any(matches(r, t) for t in terms)]
    rows.sort(key=lambda r: (r["start_date"], r["event_id"]), reverse=True)
    return jsonify(updated_at=store["updated_at"], results=rows, not_found=not_found)


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
