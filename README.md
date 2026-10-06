# Arabian Horse Championship Results

A small web app that reads championship results from
[arabianessence.tv](https://www.arabianessence.tv/) and lists them by medal.

Pick **Gold**, **Silver** or **Bronze** (and optionally one event) and the app shows,
for every matching horse: horse name, championship, result, event, start date,
end date, source page and any videos.

## Run it on your computer (VS Code)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000.

You can also try the scraper on its own:

```bash
python scraper.py gold
python scraper.py silver https://www.arabianessence.tv/events/<event-slug>/<id>/
```

## Deploy on Render

1. Push this folder to a GitHub repo.
2. Go to https://dashboard.render.com, click **New +** then **Blueprint**.
3. Connect your GitHub account and pick this repo. Render reads `render.yaml`.
4. Click **Apply**. When the build finishes, Render gives you a URL like
   `https://arabian-championship-results.onrender.com`.

(Alternative: **New + → Web Service**, pick the repo, build command
`pip install -r requirements.txt`, start command
`gunicorn app:app --workers 1 --threads 4 --timeout 180 --bind 0.0.0.0:$PORT`.)

## API

- `GET /api/events` lists events found on the site.
- `GET /api/results?medal=gold[&event=<event url>][&max_events=5]` returns results.

Results are cached for 6 hours (`CACHE_TTL_SECONDS`) so the source site is only
crawled once in a while. The first search after a restart takes longer.
