# A Share Portfolio Brief

Daily pre-market intelligence brief for a personal A-share, Hong Kong, ETF, LOF, and QDII portfolio.

The main workflow lives under `portfolio-intelligence/`:

- `config/holdings.yaml`: portfolio holdings, watch metrics, and risk rules.
- `config/sources.yaml`: report settings and market-watch configuration.
- `config/metric_sources.yaml`: automated proxy metrics and metric aliases.
- `scripts/run_daily_brief.py`: generates the Markdown report and renders the HTML UI.
- `reports/YYYY-MM-DD.md`: daily Markdown reports.
- `ui/latest.html`: latest rendered report UI.

## Run Locally

```bash
python3 -m pip install -r requirements.txt
python3 portfolio-intelligence/scripts/run_daily_brief.py
```

## GitHub Actions

The scheduled workflow runs on weekdays at `01:00 UTC`, which is `09:00 Asia/Shanghai`.

After each run, check:

- `portfolio-intelligence/reports/YYYY-MM-DD.md`
- `portfolio-intelligence/ui/latest.html`
- the workflow artifact named `daily-brief-<run_id>`

To email `latest.html` after each run, configure these repository secrets:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `MAIL_FROM` (optional; defaults to `SMTP_USERNAME`)

The report is for personal research and risk management only. It is not investment advice.
