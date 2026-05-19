#!/usr/bin/env python3
"""Render the latest Markdown portfolio brief into a standalone responsive HTML UI."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
UI_DIR = ROOT / "ui"


def latest_report() -> Path:
    reports = sorted(REPORTS_DIR.glob("20*.md"))
    if not reports:
        raise FileNotFoundError(f"No Markdown reports found under {REPORTS_DIR}")
    return reports[-1]


def parse_inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<a href="\2" target="_blank" rel="noreferrer">\1</a>',
        escaped,
    )


def strip_markdown(text: str) -> str:
    return re.sub(r"\*\*|\[|\]\([^)]+\)", "", text).strip()


def split_table_row(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_table(lines: List[str], start: int) -> Tuple[List[Dict[str, str]], int]:
    headers = split_table_row(lines[start])
    rows: List[Dict[str, str]] = []
    i = start + 2
    while i < len(lines) and lines[i].startswith("|"):
        cells = split_table_row(lines[i])
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
        i += 1
    return rows, i


def get_section(lines: List[str], heading: str) -> List[str]:
    start = None
    for i, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            start = i + 1
            break
    if start is None:
        return []
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return lines[start:end]


def parse_report(path: Path) -> Dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = lines[0].lstrip("# ").strip() if lines else path.stem
    meta = []
    for line in lines[1:8]:
        if line.startswith("- "):
            meta.append(line[2:])

    quality_lines = get_section(lines, "数据质量")
    quality_summary = next((x for x in quality_lines if x and not x.startswith("|") and not x.startswith("- ")), "")
    quality_table: List[Dict[str, str]] = []
    for i, line in enumerate(quality_lines):
        if line.startswith("| 类型 |"):
            quality_table, _ = parse_table(quality_lines, i)
            break

    market_lines = get_section(lines, "A股大盘盘前观察")
    market_table: List[Dict[str, str]] = []
    market_note = ""
    for i, line in enumerate(market_lines):
        if line.startswith("| 指数 |"):
            market_table, end = parse_table(market_lines, i)
            rest = [x[2:] for x in market_lines[end:] if x.startswith("- ")]
            market_note = " ".join(rest)
            break

    overseas_lines = get_section(lines, "隔夜海外与宏观风险")
    overseas = [x for x in overseas_lines if x.strip()]

    context_lines = get_section(lines, "上下文指标证据")
    context_table: List[Dict[str, str]] = []
    for i, line in enumerate(context_lines):
        if line.startswith("| 指标 |"):
            context_table, _ = parse_table(context_lines, i)
            break

    holdings = parse_holdings(lines)
    action_counts: Dict[str, int] = {}
    for item in holdings:
        action_counts[item["action"]] = action_counts.get(item["action"], 0) + 1

    return {
        "title": title,
        "date": path.stem,
        "meta": meta,
        "quality_summary": quality_summary,
        "quality_table": quality_table,
        "market_table": market_table,
        "market_note": market_note,
        "overseas": overseas,
        "context_table": context_table,
        "holdings": holdings,
        "action_counts": action_counts,
    }


def parse_holdings(lines: List[str]) -> List[Dict[str, Any]]:
    holdings: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    title_re = re.compile(r"^### (.+?)（(.+?)，(.+?)，(.+?)）$")
    for line in lines:
        match = title_re.match(line)
        if match:
            if current:
                holdings.append(finalize_holding(current))
            current = {
                "name": match.group(1),
                "code": match.group(2),
                "market": match.group(3),
                "asset_type": match.group(4),
                "raw": [],
            }
            continue
        if current is not None:
            if line.startswith("## "):
                break
            if line.startswith("- "):
                current["raw"].append(line[2:])
    if current:
        holdings.append(finalize_holding(current))
    return holdings


def finalize_holding(item: Dict[str, Any]) -> Dict[str, Any]:
    fields: Dict[str, str] = {}
    for raw in item.get("raw", []):
        if "：" in raw:
            key, value = raw.split("：", 1)
            fields[key.strip()] = value.strip()
    item["position"] = fields.get("持仓/成本", "")
    item["tags"] = [x.strip() for x in fields.get("主题标签", "").split(",") if x.strip()]
    item["price"] = fields.get("价格与量价", fields.get("行情", ""))
    item["news"] = fields.get("重要新闻", "")
    item["metrics"] = [x.strip() for x in fields.get("观察指标", "").split(";") if x.strip()]
    item["evidence"] = [x.strip() for x in fields.get("指标证据", "").split(";") if x.strip()]
    item["manual_review"] = [x.strip() for x in fields.get("人工复核", "").split(";") if x.strip()]
    item["rules"] = [x.strip() for x in fields.get("风险规则", "").split(";") if x.strip()]
    action_text = fields.get("今日动作框架", "")
    action = strip_markdown(action_text.split("。", 1)[0])
    item["action"] = action or "待确认"
    item["action_detail"] = action_text
    change_match = re.search(r"近一日 ([+\-]?\d+(?:\.\d+)?)%", item["price"])
    item["change"] = float(change_match.group(1)) if change_match else None
    return item


def action_class(action: str) -> str:
    if "减仓" in action or "止损" in action:
        return "risk"
    if "加仓" in action:
        return "watch"
    if "数据" in action or "确认" in action:
        return "muted"
    return "neutral"


def change_class(value: float | None) -> str:
    if value is None:
        return "muted"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def render_html(data: Dict[str, Any]) -> str:
    counts = data["action_counts"]
    total = len(data["holdings"])
    quality_cards = "\n".join(
        f"""
        <div class="metric-card">
          <span>{html.escape(row.get("类型", ""))}</span>
          <strong>{html.escape(row.get("数量", ""))}</strong>
          <small>{html.escape(row.get("处理方式", ""))}</small>
        </div>
        """
        for row in data["quality_table"]
    )
    market_rows = "\n".join(
        f"""
        <tr>
          <td>{html.escape(row.get("指数", ""))}</td>
          <td>{html.escape(row.get("最新收盘", ""))}</td>
          <td class="{change_class(parse_pct(row.get("近一日")))}">{html.escape(row.get("近一日", ""))}</td>
          <td>{html.escape(row.get("MA5", ""))}</td>
          <td>{html.escape(row.get("MA20", ""))}</td>
          <td>{html.escape(row.get("趋势", ""))}</td>
        </tr>
        """
        for row in data["market_table"]
    )
    holding_cards = "\n".join(render_holding_card(item) for item in data["holdings"])
    context_cards = "\n".join(render_context_card(row) for row in data["context_table"])
    action_chips = "\n".join(
        f'<button class="chip" data-filter="{html.escape(action)}">{html.escape(action)} <span>{count}</span></button>'
        for action, count in sorted(counts.items())
    )
    meta = "\n".join(f"<li>{parse_inline_markdown(x)}</li>" for x in data["meta"])
    overseas = "\n".join(
        f"<p>{parse_inline_markdown(x[2:] if x.startswith('- ') else x)}</p>" for x in data["overseas"]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(data["title"])}</title>
  <style>{CSS}</style>
</head>
<body>
  <header class="app-header">
    <div>
      <p class="eyebrow">Portfolio Intelligence</p>
      <h1>{html.escape(data["title"])}</h1>
      <ul class="meta">{meta}</ul>
    </div>
    <div class="hero-metrics">
      <div><span>持仓标的</span><strong>{total}</strong></div>
      <div><span>减仓警戒</span><strong>{counts.get("减仓警戒", 0)}</strong></div>
      <div><span>继续观察</span><strong>{counts.get("继续观察", 0)}</strong></div>
    </div>
  </header>

  <main class="page-shell">
    <section class="band quality">
      <div class="section-head">
        <p class="eyebrow">Data Quality</p>
        <h2>数据质量</h2>
      </div>
      <p class="summary">{html.escape(data["quality_summary"])}</p>
      <div class="metric-grid">{quality_cards}</div>
    </section>

    <section class="band context-band">
      <div class="section-head">
        <p class="eyebrow">Evidence</p>
        <h2>上下文指标证据</h2>
      </div>
      <div class="context-grid">{context_cards or '<p class="summary">今日未取得可用的上下文代理指标。</p>'}</div>
    </section>

    <section class="content-grid">
      <div class="panel market-panel">
        <div class="section-head">
          <p class="eyebrow">Market</p>
          <h2>A股大盘盘前观察</h2>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>指数</th><th>最新收盘</th><th>近一日</th><th>MA5</th><th>MA20</th><th>趋势</th></tr></thead>
            <tbody>{market_rows}</tbody>
          </table>
        </div>
        <p class="note">{html.escape(data["market_note"])}</p>
      </div>

      <aside class="panel overseas-panel">
        <div class="section-head">
          <p class="eyebrow">Overnight</p>
          <h2>海外与宏观风险</h2>
        </div>
        <div class="copy">{overseas}</div>
      </aside>
    </section>

    <section class="holdings-section">
      <div class="toolbar">
        <div>
          <p class="eyebrow">Holdings</p>
          <h2>持仓标的风险框架</h2>
        </div>
        <label class="search">
          <span>搜索</span>
          <input id="searchInput" type="search" placeholder="名称 / 代码 / 主题">
        </label>
      </div>
      <div class="chips">
        <button class="chip active" data-filter="all">全部 <span>{total}</span></button>
        {action_chips}
      </div>
      <div class="cards" id="cards">{holding_cards}</div>
    </section>
  </main>

  <script>{JS}</script>
</body>
</html>
"""


def parse_pct(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def render_holding_card(item: Dict[str, Any]) -> str:
    tags = "\n".join(f"<span>{html.escape(tag)}</span>" for tag in item["tags"])
    metrics = "\n".join(f"<li>{html.escape(metric)}</li>" for metric in item["metrics"])
    evidence = "\n".join(f"<li>{html.escape(metric)}</li>" for metric in item["evidence"])
    manual_review = "\n".join(f"<li>{html.escape(metric)}</li>" for metric in item["manual_review"])
    rules = "\n".join(f"<li>{html.escape(rule)}</li>" for rule in item["rules"])
    action = html.escape(item["action"])
    cls = action_class(item["action"])
    change = item["change"]
    change_text = "N/A" if change is None else f"{change:+.2f}%"
    news = parse_inline_markdown(item["news"])
    price = html.escape(item["price"])
    search = " ".join([item["name"], item["code"], item["market"], item["asset_type"], *item["tags"]])
    return f"""
      <article class="holding-card" data-action="{html.escape(item["action"])}" data-search="{html.escape(search.lower())}">
        <div class="card-top">
          <div>
            <h3>{html.escape(item["name"])}</h3>
            <p>{html.escape(item["code"])} · {html.escape(item["market"])} · {html.escape(item["asset_type"])}</p>
          </div>
          <span class="status {cls}">{action}</span>
        </div>
        <div class="quote-row">
          <div><span>持仓/成本</span><strong>{html.escape(item["position"])}</strong></div>
          <div><span>近一日</span><strong class="{change_class(change)}">{html.escape(change_text)}</strong></div>
        </div>
        <p class="price-line">{price}</p>
        <div class="tags">{tags}</div>
        <details class="news" open>
          <summary>重要新闻</summary>
          <p>{news}</p>
        </details>
        <div class="two-col">
          <div>
            <h4>指标证据</h4>
            <ul>{evidence or "<li>暂无可自动化证据</li>"}</ul>
          </div>
          <div>
            <h4>人工复核</h4>
            <ul>{manual_review or "<li>无</li>"}</ul>
          </div>
        </div>
        <details class="metric-details">
          <summary>观察指标与风险规则</summary>
          <div class="two-col inner">
            <div>
              <h4>观察指标</h4>
              <ul>{metrics}</ul>
            </div>
            <div>
              <h4>风险规则</h4>
              <ul>{rules}</ul>
            </div>
          </div>
        </details>
        <p class="action-detail">{parse_inline_markdown(item["action_detail"])}</p>
      </article>
    """


def render_context_card(row: Dict[str, str]) -> str:
    status = row.get("状态", "")
    cls = "up" if "偏强" in status else "down" if "偏弱" in status else "flat"
    change = row.get("变化", "")
    return f"""
        <div class="context-card">
          <span>{html.escape(row.get("指标", ""))}</span>
          <strong>{html.escape(row.get("最新值", ""))}</strong>
          <small class="{cls}">{html.escape(change)} · {html.escape(status)}</small>
        </div>
    """


CSS = r"""
:root {
  color-scheme: light;
  --bg: #f5f7fb;
  --ink: #152033;
  --muted: #657084;
  --line: #dfe5ef;
  --panel: #ffffff;
  --accent: #1457d9;
  --accent-soft: #e8f0ff;
  --red: #c93636;
  --red-soft: #fff0f0;
  --green: #15804d;
  --green-soft: #e9f8f1;
  --amber: #a66900;
  --amber-soft: #fff6df;
  --shadow: 0 16px 45px rgba(26, 38, 70, 0.10);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  color: var(--ink);
  background: var(--bg);
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.app-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 28px;
  padding: 38px max(24px, calc((100vw - 1200px) / 2)) 30px;
  color: #fff;
  background:
    linear-gradient(120deg, rgba(12, 26, 50, 0.93), rgba(24, 67, 137, 0.92)),
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='240' height='120' viewBox='0 0 240 120'%3E%3Cg fill='none' stroke='%23ffffff' stroke-opacity='.13'%3E%3Cpath d='M0 90 C45 25 70 90 120 45 S198 20 240 62'/%3E%3Cpath d='M0 40 C58 70 82 8 132 32 S200 92 240 25'/%3E%3C/g%3E%3C/svg%3E");
  background-size: cover, 360px 180px;
}
.app-header h1 {
  margin: 4px 0 14px;
  font-size: clamp(28px, 5vw, 48px);
  line-height: 1.08;
  letter-spacing: 0;
}
.eyebrow {
  margin: 0;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0;
  color: var(--accent);
  text-transform: uppercase;
}
.app-header .eyebrow { color: #a8c7ff; }
.meta {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
  color: rgba(255,255,255,.82);
  font-size: 14px;
}
.hero-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(108px, 1fr));
  gap: 10px;
  align-self: end;
}
.hero-metrics div {
  padding: 14px;
  border: 1px solid rgba(255,255,255,.20);
  border-radius: 8px;
  background: rgba(255,255,255,.10);
}
.hero-metrics span,
.metric-card span,
.quote-row span {
  display: block;
  color: var(--muted);
  font-size: 12px;
}
.hero-metrics span { color: rgba(255,255,255,.72); }
.hero-metrics strong {
  display: block;
  margin-top: 6px;
  font-size: 28px;
}
.page-shell {
  width: min(1200px, calc(100% - 32px));
  margin: 24px auto 56px;
}
.band,
.panel,
.holding-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--shadow);
}
.band { padding: 22px; }
.context-band { margin-top: 16px; }
.section-head {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
h2, h3, h4 { margin: 0; letter-spacing: 0; }
h2 { font-size: 22px; }
.summary, .note, .copy p {
  margin: 0 0 14px;
  color: var(--muted);
  line-height: 1.7;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.metric-card {
  min-height: 112px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcff;
}
.metric-card strong {
  display: block;
  margin: 8px 0;
  font-size: 32px;
}
.metric-card small {
  color: var(--muted);
  line-height: 1.45;
}
.context-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.context-card {
  min-height: 104px;
  padding: 15px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcff;
}
.context-card span {
  display: block;
  color: var(--muted);
  font-size: 12px;
}
.context-card strong {
  display: block;
  margin: 9px 0 7px;
  font-size: 22px;
}
.context-card small {
  font-weight: 800;
}
.content-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(280px, .75fr);
  gap: 16px;
  margin-top: 16px;
}
.panel { padding: 22px; min-width: 0; }
.table-wrap { overflow-x: auto; }
table {
  width: 100%;
  border-collapse: collapse;
  min-width: 680px;
  font-size: 14px;
}
th, td {
  padding: 12px 10px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  white-space: nowrap;
}
th {
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  background: #f8faff;
}
.holdings-section { margin-top: 22px; }
.toolbar {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 0;
  background: rgba(245,247,251,.92);
  backdrop-filter: blur(14px);
}
.search {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--muted);
  font-size: 13px;
}
.search input {
  width: min(360px, 48vw);
  height: 40px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0 12px;
  font: inherit;
  color: var(--ink);
  background: #fff;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 4px 0 16px;
}
.chip {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 8px 12px;
  color: var(--ink);
  background: #fff;
  cursor: pointer;
}
.chip.active {
  color: #fff;
  border-color: var(--accent);
  background: var(--accent);
}
.chip span { color: inherit; opacity: .75; }
.cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.holding-card {
  padding: 18px;
  min-width: 0;
}
.card-top {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 12px;
}
.card-top h3 { font-size: 20px; }
.card-top p {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.status {
  flex: 0 0 auto;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
}
.status.risk { color: var(--red); background: var(--red-soft); }
.status.watch { color: var(--amber); background: var(--amber-soft); }
.status.neutral { color: var(--accent); background: var(--accent-soft); }
.status.muted { color: var(--muted); background: #f1f3f7; }
.quote-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 92px;
  gap: 12px;
  margin: 16px 0 12px;
}
.quote-row div {
  padding: 12px;
  border-radius: 8px;
  background: #f8faff;
  min-width: 0;
}
.quote-row strong {
  display: block;
  margin-top: 6px;
  font-size: 15px;
  overflow-wrap: anywhere;
}
.up { color: var(--red); }
.down { color: var(--green); }
.flat, .muted { color: var(--muted); }
.price-line {
  margin: 0 0 12px;
  color: var(--muted);
  line-height: 1.65;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}
.tags span {
  padding: 5px 8px;
  border-radius: 6px;
  color: #28425f;
  background: #edf4fb;
  font-size: 12px;
}
.news {
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  padding: 11px 0;
}
.metric-details {
  margin-top: 12px;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}
.metric-details summary {
  cursor: pointer;
  color: var(--muted);
  font-weight: 800;
}
.two-col.inner {
  margin-top: 12px;
}
.news summary {
  cursor: pointer;
  font-weight: 800;
}
.news p {
  margin: 10px 0 0;
  color: var(--muted);
  line-height: 1.65;
}
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-top: 14px;
}
.two-col h4 {
  margin-bottom: 8px;
  font-size: 14px;
}
.two-col ul {
  display: grid;
  gap: 7px;
  margin: 0;
  padding-left: 18px;
  color: var(--muted);
  line-height: 1.5;
}
.action-detail {
  margin: 14px 0 0;
  padding: 12px;
  border-left: 3px solid var(--accent);
  border-radius: 0 8px 8px 0;
  background: #f6f9ff;
  line-height: 1.65;
}
.hidden { display: none !important; }
@media (max-width: 900px) {
  .app-header { grid-template-columns: 1fr; }
  .hero-metrics, .metric-grid, .context-grid, .content-grid, .cards { grid-template-columns: 1fr; }
  .panel { padding: 18px; }
  .toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .search { align-items: stretch; flex-direction: column; gap: 6px; }
  .search input { width: 100%; }
}
@media (max-width: 560px) {
  .app-header {
    padding: 28px 16px 24px;
  }
  .page-shell {
    width: calc(100% - 20px);
    margin-top: 14px;
  }
  .band, .panel, .holding-card { padding: 14px; }
  .hero-metrics {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .hero-metrics div { padding: 10px; }
  .hero-metrics strong { font-size: 22px; }
  .card-top { align-items: stretch; flex-direction: column; }
  .status { width: fit-content; }
  .quote-row, .two-col { grid-template-columns: 1fr; }
  h2 { font-size: 19px; }
  .card-top h3 { font-size: 18px; }
}
"""


JS = r"""
const searchInput = document.getElementById('searchInput');
const chips = Array.from(document.querySelectorAll('.chip'));
const cards = Array.from(document.querySelectorAll('.holding-card'));
let activeFilter = 'all';

function applyFilters() {
  const q = (searchInput.value || '').trim().toLowerCase();
  cards.forEach(card => {
    const matchesText = !q || card.dataset.search.includes(q);
    const matchesFilter = activeFilter === 'all' || card.dataset.action === activeFilter;
    card.classList.toggle('hidden', !(matchesText && matchesFilter));
  });
}

searchInput.addEventListener('input', applyFilters);
chips.forEach(chip => {
  chip.addEventListener('click', () => {
    chips.forEach(item => item.classList.remove('active'));
    chip.classList.add('active');
    activeFilter = chip.dataset.filter;
    applyFilters();
  });
});
"""


def main() -> int:
    report = latest_report()
    data = parse_report(report)
    UI_DIR.mkdir(parents=True, exist_ok=True)
    html_text = render_html(data)
    dated = UI_DIR / f"{report.stem}.html"
    latest = UI_DIR / "latest.html"
    dated.write_text(html_text, encoding="utf-8")
    latest.write_text(html_text, encoding="utf-8")
    print(f"Rendered UI: {dated}")
    print(f"Latest UI: {latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
