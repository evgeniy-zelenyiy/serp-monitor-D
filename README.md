# Python SERP Monitoring System

A clean, scheduled Google SERP monitoring system for reputation management and SERM. It collects Google mentions through DataForSEO when credentials are available, falls back to deterministic demo SERP data when they are not, stores historical ranking data in SQLite, classifies sentiment and risk, captures screenshots with Playwright, sends Telegram alerts, and produces an entity map JSON.

## Features

- Collect Google SERP mentions for configured brand and reputation queries.
- Run in demo mode without DataForSEO credentials so CI still validates the full pipeline.
- Save every mention and run to SQLite history.
- Detect new URLs and ranking changes by query and URL.
- Analyze sentiment as `positive`, `neutral`, `negative`, or `risky`.
- Flag Portuguese negative keywords such as `golpe`, `fraude`, `denúncia`, `reclamação`, and `reclame aqui`.
- Capture local screenshots of result URLs with Playwright.
- Send Telegram reports and screenshots for risky/negative alerts.
- Build an entity map JSON linking queries, URLs, domains, ranks, and sentiment.
- Run automatically with GitHub Actions every 3 days.

## Project structure

```text
app/
  main.py              # CLI orchestration
  serp_fetcher.py      # DataForSEO Google organic SERP API client + demo fallback
  sentiment.py         # OpenAI + Portuguese keyword sentiment/risk analysis
  screenshots.py       # Playwright screenshot capture
  telegram_report.py   # Telegram reporting
  entity_map.py        # Entity map JSON graph builder
  database.py          # SQLite persistence and change detection
  config_loader.py     # YAML and environment config loading
config.yaml            # Monitoring configuration
requirements.txt       # Python dependencies
.env.example           # Required environment variables
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

3. Copy the example environment file and fill in credentials as needed:

   ```bash
   cp .env.example .env
   ```

   Optional variables:

   - `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` enable live Google SERP collection.
   - `OPENAI_API_KEY` enables OpenAI sentiment classification; without it, keyword sentiment still runs.
   - `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` enable Telegram delivery; without them, reporting is skipped after the report is prepared.

4. Edit `config.yaml`:

   - Add brand, executive, product, and risk queries under `monitoring.queries`.
   - Set `location_code` and `language_code` for the target market.
   - Tune screenshot limits and Telegram alert behavior.
   - Set `monitoring.demo_results_per_query` for fallback mode.
   - Add more Portuguese negative keywords for your domain.

## Running locally

```bash
python -m app.main --config config.yaml
```

If `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` are missing, the command runs in demo mode and generated reports are marked `DEMO MODE - no live Google data`.

Outputs are written under `data/` by default:

- `data/serp_history.sqlite3` stores all mentions and ranking history.
- `data/screenshots/` stores Playwright screenshots.
- `data/entity_map.json` stores a graph-friendly map of query/domain/URL relationships.

## GitHub Actions

The workflow in `.github/workflows/monitor.yml` runs every 3 days and can also be triggered manually with `workflow_dispatch`.

Add these repository secrets for live monitoring and delivery:

- `DATAFORSEO_LOGIN`
- `DATAFORSEO_PASSWORD`
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The workflow succeeds without these secrets by using demo SERP data and uploading the generated SQLite, entity map, and screenshot artifacts. Optionally add the repository variable `OPENAI_MODEL`; otherwise the workflow uses `gpt-4o-mini`.

## Notes

- The monitor compares each `(query, url)` pair against the latest previous rank to detect new URLs and position movement.
- Demo mode keeps DataForSEO support intact and only activates when login or password is missing.
- If OpenAI classification is unavailable, the system still runs with the local Portuguese negative keyword heuristic.
- Screenshot failures are logged but do not stop mention collection or alerting.
