# Arabian Horse Championship Results

A small web app that reads championship results from
[arabianessence.tv](https://www.arabianessence.tv/) and lists them by medal.

Pick **Platinum**, **Gold**, **Silver** or **Bronze** (and optionally one event) and the app shows,
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

## How results stay up to date

A GitHub Actions job (`.github/workflows/update.yml`) runs every hour. It checks
arabianessence.tv for events that have started, reads each event's
Championships section (Gold / Silver / Bronze), the Platinum titles in its Awards section
(only some shows, such as the World Championship, have them) and the class videos, and saves:

- `data/results.json` with every result (the website reads this file)
- `data/results.xlsx` with the same results plus a **Changes** sheet
- `data/changes.json` with each new or changed win and when it was found

When something changes, the job commits the new files. Render then redeploys
the site with the new data automatically.

To load older years, open the repo's **Actions** tab, pick **Update results**,
click **Run workflow** and enter years such as `2023 2024`. Tick **recheck_all** to
re-read every saved event, for example after the scraper learns to read something new.

To refresh the data on your own computer: `python update_results.py`.

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

- `GET /api/events` lists events that have results.
- `GET /api/results?medal=gold[&event=<event id>][&q=<text>]` returns results.
- `GET /api/changes` lists the latest new or changed wins.
- `GET /download/results.xlsx` downloads the Excel sheet.
