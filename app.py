import os

from flask import Flask, jsonify, render_template, request

import scraper

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/events")
def api_events():
    try:
        events = scraper.list_events()
    except Exception as exc:  # network or parsing failure on the source site
        return jsonify(error=str(exc)), 502
    return jsonify([e.__dict__ for e in events])


@app.get("/api/results")
def api_results():
    medal = request.args.get("medal", "gold")
    event_url = request.args.get("event") or None
    max_events = min(int(request.args.get("max_events", 5)), 20)
    if event_url and not event_url.startswith(scraper.BASE_URL):
        return jsonify(error="event must be an arabianessence.tv URL"), 400
    try:
        results = scraper.search(medal, event_url, max_events)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as exc:
        return jsonify(error=str(exc)), 502
    return jsonify(results)


@app.get("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
