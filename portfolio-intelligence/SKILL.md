---
name: portfolio-intelligence
description: Generate a daily A-share and portfolio intelligence brief for the user's iCloud investment workspace. Use when the user asks for A-share market morning briefings, portfolio risk frameworks, holdings analysis, ETF/stock watchlists, or daily 09:15 investment preparation reports.
---

# Portfolio Intelligence

This project-local skill produces a daily 09:15 China-time Markdown briefing for the user's securities portfolio.

## Core workflow

1. Read `config/holdings.yaml` as the source of truth for holdings.
2. Read `config/sources.yaml` for data source and report settings.
3. Run `scripts/run_daily_brief.py`.
4. Review the generated report under `reports/YYYY-MM-DD.md`.

## Output principles

- Treat the report as pre-market preparation, not real-time trading advice.
- Use free public data sources first, especially AkShare-accessible market data.
- Do not fabricate missing market data or news. If a source fails, record the failure in the report.
- Produce condition-based risk frameworks instead of unconditional buy/sell instructions.
- Keep holdings extensible: add, remove, or edit instruments in `config/holdings.yaml`.

## Holding analysis frame

For each holding, cover:

- Key observations: price trend, volume signal, theme or sector context, and available news.
- Watch metrics: the instrument-specific metrics listed in `holdings.yaml`.
- Risk rules: measurable triggers for review, add-watch, trim-watch, and stop-review.
- Action frame: one of `continue_watch`, `add_watch`, `trim_warning`, or `stop_review`.

## Manual run

```bash
python3 portfolio-intelligence/scripts/run_daily_brief.py
```

The report is written to:

```text
portfolio-intelligence/reports/YYYY-MM-DD.md
```

