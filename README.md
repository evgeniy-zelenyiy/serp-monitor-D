# Python SERP Monitoring System

A clean, scheduled Google SERP monitoring system for reputation management and SERM. It collects live Google organic top-10 snapshots through Serper.dev when `SERPER_API_KEY` is available, falls back to deterministic demo SERP data when it is not, stores historical ranking data in SQLite, classifies sentiment and risk, renders SERP screenshots with Playwright, sends Telegram alerts for meaningful changes, exports a static GitHub Pages dashboard, and produces an entity map JSON.

## Features

- Collect Google organic SERP top-10 snapshots for configured brand and reputation queries with Serper.dev.
- Run in demo mode without `SERPER_API_KEY` so CI still validates the full pipeline.
- Save each workflow run and each query snapshot to SQLite with region, language, device, source type, and run datetime.
- Preserve `first_seen` and `last_seen` by `(query, url)` and track `previous_rank`, `rank_delta`, and `disappeared_at`.
- Mark URLs as `new`, `existing`, `changed`, or `disappeared` from historical snapshot data, not from dashboard export order.
- Analyze sentiment as `positive`, `neutral`, `negative`, or `risky`.
- Flag Portuguese negative keywords such as `golpe`, `fraude`, `denúncia`, `reclamação`, and `reclame aqui`.
- Try to detect article publication dates from JSON-LD, OpenGraph/meta tags, `time` elements, and visible date patterns.
- Render one local SERP top-10 screenshot per query/run with filenames such as `2026-05-14/dmytro-rukin-br-pt-top10.png`.
- Send Telegram reports only for new URLs, rank changes, risky/negative mentions, and disappeared URLs.
- Build an entity map JSON linking queries, URLs, domains, ranks, source type, and sentiment.
- Publish a permanent static dashboard from the `/docs` folder with query tabs, snapshot views, filters, sorting, domain summaries, and screenshots.
- Run automatically with GitHub Actions every 6 hours.

## Project structure

```text
app/                       # Monitor, persistence, sentiment, screenshots, reports
scripts/export_dashboard_data.py
docs/
  index.html               # GitHub Pages dashboard
  styles.css               # Dark responsive dashboard styles
  app.js                   # Tabs, filters, sorting, rendering
  data/results.json        # Auto-updated dashboard data
  screenshots/             # Auto-copied dashboard SERP screenshots
config.yaml
requirements.txt
.env.example
.github/workflows/monitor.yml
```

## Setup

1. Create a virtual environment with Python 3.11:

   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies and the Chromium browser used by Playwright:

   ```bash
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

3. Create a Serper.dev API key, then copy the example environment file and fill in credentials as needed:

   ```bash
   cp .env.example .env
   ```

   Optional variables:

   - `SERPER_API_KEY` enables live Google organic result collection through Serper.dev.
   - `OPENAI_API_KEY` enables OpenAI sentiment classification; without it, keyword sentiment still runs.
   - `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` enable Telegram delivery; without them, reporting is skipped after the report is prepared.

4. Edit `config.yaml`:

   - Add brand, executive, product, and risk queries under `monitoring.queries`.
   - Set `country`, `language`, `location_name`, and `device` for the target market.
   - Set `monitoring.depth` to `10` for top-10 organic snapshot tracking.
   - Keep `monitoring.screenshot_mode` as `serp` to capture SERP snapshots instead of article pages.
   - Keep `monitoring.track_disappeared` enabled to record URLs that leave the top 10.
   - Keep `monitoring.extract_publication_date` enabled to enrich article rows when possible.
   - Set `monitoring.demo_results_per_query` for fallback mode.
   - Add more Portuguese negative keywords for your domain.

## Running locally

```bash
python -m app.main --config config.yaml
python scripts/export_dashboard_data.py --config config.yaml --docs-dir docs
```

If `SERPER_API_KEY` is missing, the command runs in demo mode and generated reports are marked `DEMO MODE - no live Google data`.

Outputs are written under `data/` and `docs/` by default:

- `data/serp_history.sqlite3` stores all runs and SERP snapshots.
- `data/screenshots/YYYY-MM-DD/` stores rendered SERP top-10 screenshots.
- `data/entity_map.json` stores a graph-friendly map of query/domain/URL relationships.
- `docs/data/results.json` stores the dashboard-ready history export.
- `docs/screenshots/YYYY-MM-DD/` stores dashboard screenshot copies.

## GitHub Pages Dashboard

The dashboard is a static site served from the repository `/docs` folder. It uses a single permanent GitHub Pages URL and refreshes automatically when `.github/workflows/monitor.yml` commits updated files after each run.

To enable it:

1. Open the repository on GitHub.
2. Go to `Settings` -> `Pages`.
3. Set `Source` to `Deploy from a branch`.
4. Select branch `main` and folder `/docs`.
5. Save the settings.

After GitHub Pages finishes publishing, use the Pages URL as the permanent dashboard link. The workflow updates `docs/data/results.json` and `docs/screenshots/` every 6 hours and on manual `workflow_dispatch` runs.

The dashboard includes:

- Query tabs for the current top-10 by configured query.
- Global views for all mentions, new URLs, rank changes, and disappeared URLs.
- Filters for query, date, status, sentiment, risk level, and domain.
- Sorting by rank, first seen, last seen, sentiment, and risk level.
- A screenshot gallery grouped by capture date and query.
- Entity/domain summaries from the latest snapshots and `entity_map.json`.

## GitHub Actions

The workflow in `.github/workflows/monitor.yml` runs every 6 hours and can also be triggered manually with `workflow_dispatch`.

Add these repository secrets for live monitoring and delivery:

- `SERPER_API_KEY`
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The workflow succeeds without these secrets by using demo SERP data, exporting dashboard data, committing updated `/docs` files back to `main`, and uploading generated artifacts. Optionally add the repository variable `OPENAI_MODEL`; otherwise the workflow uses `gpt-4o-mini`.

## Notes

- Rank is always the organic position inside the Serper.dev organic results for that query: first result is `1`, second is `2`, and so on.
- A URL is `new` only the first time the monitor sees that URL for the same query.
- A URL is `changed` only when its rank differs from the previous snapshot for the same query.
- A URL is `disappeared` when it existed in the previous top-10 for the query but is absent from the current top-10.
- `first_seen` is the first time this monitor saw a URL in the SERP. It is not the article publication date.
- `date_published` is best-effort metadata extracted from the article page and is shown as `unknown` when unavailable.
- Demo mode keeps the full pipeline available and only activates when `SERPER_API_KEY` is missing.
- If OpenAI classification is unavailable, the system still runs with the local Portuguese negative keyword heuristic.
- Screenshot failures are logged but do not stop snapshot collection, dashboard export, or alerting.
