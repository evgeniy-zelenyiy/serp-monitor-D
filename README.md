# Python SERP Monitoring System

A clean, scheduled Google SERP monitoring system for reputation management and SERM. It collects live Google organic results through Serper.dev when `SERPER_API_KEY` is available, falls back to deterministic demo SERP data when it is not, stores historical ranking data in SQLite, classifies sentiment and risk, captures screenshots with Playwright, sends Telegram alerts, exports a static GitHub Pages dashboard, and produces an entity map JSON.

## Features

- Collect Google organic SERP mentions for configured brand and reputation queries with Serper.dev.
- Run in demo mode without `SERPER_API_KEY` so CI still validates the full pipeline.
- Save every mention and run to SQLite history with `first_seen`, `last_seen`, `source_type`, `risk_level`, and `risk_keywords`.
- Detect new URLs and ranking changes by query and URL.
- Analyze sentiment as `positive`, `neutral`, `negative`, or `risky`.
- Flag Portuguese negative keywords such as `golpe`, `fraude`, `denúncia`, `reclamação`, and `reclame aqui`.
- Capture local screenshots of result URLs with Playwright.
- Send Telegram reports and screenshots for risky/negative alerts.
- Build an entity map JSON linking queries, URLs, domains, ranks, source type, and sentiment.
- Publish a permanent static dashboard from the `/docs` folder with sortable tables, filters, summary cards, and screenshots.
- Run automatically with GitHub Actions every 6 hours.

## Project structure

```text
app/
  main.py               # CLI orchestration
  serp_fetcher.py       # Serper.dev Google organic SERP API client + demo fallback
  sentiment.py          # OpenAI + Portuguese keyword sentiment/risk analysis
  screenshots.py        # Playwright screenshot capture
  telegram_report.py    # Telegram reporting
  entity_map.py         # Entity map JSON graph builder
  database.py           # SQLite persistence and change detection
  config_loader.py      # YAML and environment config loading
  dashboard_exporter.py # Static dashboard JSON and screenshot exporter
docs/
  index.html            # GitHub Pages dashboard
  styles.css            # Dashboard styles
  app.js                # Dashboard filters, sorting, and rendering
  data/results.json     # Auto-updated dashboard data
config.yaml             # Monitoring configuration
requirements.txt        # Python dependencies
.env.example            # Environment variables
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
   - Set `country`, `language`, and `location_name` for the target market.
   - Set `depth` for the number of organic results to collect per query.
   - Tune screenshot limits and Telegram alert behavior.
   - Set `monitoring.demo_results_per_query` for fallback mode.
   - Add more Portuguese negative keywords for your domain.

## Running locally

```bash
python -m app.main --config config.yaml
python -m app.dashboard_exporter --config config.yaml --docs-dir docs
```

If `SERPER_API_KEY` is missing, the command runs in demo mode and generated reports are marked `DEMO MODE - no live Google data`.

Outputs are written under `data/` and `docs/data/` by default:

- `data/serp_history.sqlite3` stores all mentions and ranking history.
- `data/screenshots/` stores Playwright screenshots.
- `data/entity_map.json` stores a graph-friendly map of query/domain/URL relationships.
- `docs/data/results.json` stores the dashboard-ready history export.
- `docs/data/screenshots/` stores dashboard screenshot copies.

## GitHub Pages Dashboard

The dashboard is a static site served from the repository `/docs` folder. It uses a single permanent GitHub Pages URL and refreshes automatically when `.github/workflows/monitor.yml` commits updated files after each run.

To enable it:

1. Open the repository on GitHub.
2. Go to `Settings` -> `Pages`.
3. Set `Source` to `Deploy from a branch`.
4. Select branch `main` and folder `/docs`.
5. Save the settings.

After GitHub Pages finishes publishing, use the Pages URL as the permanent dashboard link. The workflow updates `docs/data/results.json` and `docs/data/screenshots/` every 6 hours and on manual `workflow_dispatch` runs.

## GitHub Actions

The workflow in `.github/workflows/monitor.yml` runs every 6 hours and can also be triggered manually with `workflow_dispatch`.

Add these repository secrets for live monitoring and delivery:

- `SERPER_API_KEY`
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The workflow succeeds without these secrets by using demo SERP data, exporting dashboard data, committing updated `/docs` files back to `main`, and uploading generated artifacts. Optionally add the repository variable `OPENAI_MODEL`; otherwise the workflow uses `gpt-4o-mini`.

## Notes

- The monitor compares each `(query, url)` pair against the latest previous rank to detect new URLs and position movement.
- `first_seen` is preserved from the earliest observation for each `(query, url)` pair; `last_seen` updates on every run where that mention appears.
- Demo mode keeps the full pipeline available and only activates when `SERPER_API_KEY` is missing.
- If OpenAI classification is unavailable, the system still runs with the local Portuguese negative keyword heuristic.
- Screenshot failures are logged but do not stop mention collection, dashboard export, or alerting.
