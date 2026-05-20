#!/usr/bin/env python3
"""Generate a local Markdown morning brief for the portfolio."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import socket
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
REPORTS_DIR = ROOT / "reports"
SPOT_CACHE: Dict[str, Any] = {}
METRIC_CACHE: Dict[str, Any] = {}
NETWORK_CHECK_HOSTS = [
    "finance.sina.com.cn",
    "push2his.eastmoney.com",
    "search-api-web.eastmoney.com",
    "web.ifzq.gtimg.cn",
]


def load_structured(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return data or {}
    except Exception:
        return json.loads(text)


def now_cn() -> dt.datetime:
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        return dt.datetime.now()


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isnan(float(value)):
            return None
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"", "-", "nan", "None"}:
        return None
    try:
        return float(text)
    except Exception:
        return None


def fmt_num(value: Optional[float], digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{digits}f}{suffix}"


def pct(value: Optional[float]) -> str:
    return fmt_num(value, 2, "%")


def import_akshare(notes: List[str]):
    if os.environ.get("PORTFOLIO_INTEL_DISABLE_AKSHARE") == "1":
        notes.append("AkShare disabled by PORTFOLIO_INTEL_DISABLE_AKSHARE=1 for fallback testing.")
        return None
    try:
        import akshare as ak  # type: ignore

        return ak
    except Exception as exc:
        notes.append(f"AkShare unavailable: {exc}")
        return None


def network_preflight(notes: List[str]) -> bool:
    resolved_hosts = []
    failed_hosts = []
    for host in NETWORK_CHECK_HOSTS:
        try:
            socket.getaddrinfo(host, None)
            resolved_hosts.append(host)
        except Exception as exc:
            failed_hosts.append(f"{host}: {short_error(exc)}")

    if resolved_hosts:
        return True

    if failed_hosts:
        notes.append("env network dns unavailable: no market-data hosts resolved")
        for item in failed_hosts:
            notes.append(f"env network dns detail: {item}")
    return False


def normalize_hist_frame(df: Any) -> List[Dict[str, Any]]:
    if df is None:
        return []
    try:
        records = df.tail(80).to_dict("records")
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for row in records:
        close = first_float(row, ["收盘", "close", "Close", "最新价", "收盘价"])
        open_ = first_float(row, ["开盘", "open", "Open", "开盘价"])
        high = first_float(row, ["最高", "high", "High", "最高价"])
        low = first_float(row, ["最低", "low", "Low", "最低价"])
        volume = first_float(row, ["成交量", "volume", "Volume"])
        amount = first_float(row, ["成交额", "amount", "Amount"])
        date = first_value(row, ["日期", "date", "Date", "时间"])
        if close is not None:
            out.append(
                {
                    "date": str(date) if date is not None else "",
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "amount": amount,
                }
            )
    return out


def first_value(row: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def first_float(row: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    return safe_float(first_value(row, keys))


def moving_average(values: List[float], window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def price_signal(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    closes = [x["close"] for x in history if x.get("close") is not None]
    volumes = [x["volume"] for x in history if x.get("volume") is not None]
    amounts = [x["amount"] for x in history if x.get("amount") is not None]
    if len(closes) < 2:
        return {
            "last_close": closes[-1] if closes else None,
            "change_pct": None,
            "trend": "仅有快照行情，趋势待复核" if closes else "数据不足",
            "volume_signal": "数据不足",
            "amount_signal": "数据不足",
            "state": "continue_watch" if closes else "data_unavailable",
        }

    last = closes[-1]
    prev = closes[-2]
    change = (last / prev - 1) * 100 if prev else None
    ma5 = moving_average(closes, 5)
    ma20 = moving_average(closes, 20)

    if ma5 is not None and ma20 is not None and last > ma5 > ma20:
        trend = "短中期趋势偏强"
        state = "add_watch"
    elif ma5 is not None and ma20 is not None and last < ma5 < ma20:
        trend = "短中期趋势偏弱"
        state = "trim_warning"
    elif ma20 is not None and last < ma20:
        trend = "低于20日均线，需复核"
        state = "stop_review" if change is not None and change < -3 else "trim_warning"
    else:
        trend = "趋势中性，等待确认"
        state = "continue_watch"

    volume_signal = "数据不足"
    if len(volumes) >= 6:
        avg5 = sum(volumes[-6:-1]) / 5
        if avg5 > 0 and volumes[-1] > avg5 * 1.5:
            volume_signal = "明显放量"
        elif avg5 > 0 and volumes[-1] < avg5 * 0.7:
            volume_signal = "缩量"
        else:
            volume_signal = "量能平稳"

    amount_signal = "数据不足"
    if len(amounts) >= 6:
        avg5_amount = sum(amounts[-6:-1]) / 5
        if avg5_amount > 0 and amounts[-1] > avg5_amount * 1.5:
            amount_signal = "成交额明显放大"
        elif avg5_amount > 0 and amounts[-1] < avg5_amount * 0.7:
            amount_signal = "成交额萎缩"
        else:
            amount_signal = "成交额平稳"

    return {
        "last_close": last,
        "change_pct": change,
        "ma5": ma5,
        "ma20": ma20,
        "trend": trend,
        "volume_signal": volume_signal,
        "amount_signal": amount_signal,
        "state": state,
    }


def holding_market_value(holding: Dict[str, Any], signal: Dict[str, Any]) -> Optional[float]:
    shares = safe_float(holding.get("shares"))
    price = safe_float(signal.get("last_close"))
    if shares is None or price is None or shares <= 0:
        return None
    return shares * price


def unrealized_pct(holding: Dict[str, Any], signal: Dict[str, Any]) -> Optional[float]:
    cost = safe_float(holding.get("cost"))
    price = safe_float(signal.get("last_close"))
    if cost is None or price is None or cost <= 0:
        return None
    return (price / cost - 1) * 100


def metric_state(change_pct: Optional[float]) -> str:
    if change_pct is None:
        return "neutral"
    if change_pct >= 1:
        return "positive"
    if change_pct <= -1:
        return "negative"
    return "neutral"


def metric_label(metric: Dict[str, Any]) -> str:
    value = metric.get("value")
    change = metric.get("change_pct")
    state = {
        "positive": "偏强",
        "negative": "偏弱",
        "neutral": "中性",
        "unavailable": "缺失",
    }.get(metric.get("state"), "中性")
    if value is None and change is None:
        return f"{metric.get('name')}：数据缺失"
    return f"{metric.get('name')}：{fmt_num(value)}，{pct(change)}，{state}"


def fetch_context_metrics(ak: Any, metric_config: Dict[str, Any], notes: List[str]) -> Dict[str, Dict[str, Any]]:
    metrics: Dict[str, Dict[str, Any]] = {}
    for spec in metric_config.get("global_metrics", []):
        metric_id = spec.get("id")
        if not metric_id:
            continue
        metric = fetch_one_context_metric(ak, spec, notes)
        metrics[metric_id] = metric
    return metrics


def fetch_one_context_metric(ak: Any, spec: Dict[str, Any], notes: List[str]) -> Dict[str, Any]:
    base = {
        "id": spec.get("id"),
        "name": spec.get("name"),
        "source": spec.get("source"),
        "value": None,
        "change_pct": None,
        "state": "unavailable",
    }
    if ak is None:
        return base
    try:
        source = spec.get("source")
        if source == "stock_hk_index_spot_sina":
            df = METRIC_CACHE.get(source)
            if df is None:
                df = ak.stock_hk_index_spot_sina()
                METRIC_CACHE[source] = df
            row = match_metric_row(df, spec)
            return metric_from_row(base, row, ["最新价"], ["涨跌幅"])
        if source == "stock_hsgt_fund_flow_summary_em":
            df = METRIC_CACHE.get(source)
            if df is None:
                df = ak.stock_hsgt_fund_flow_summary_em()
                METRIC_CACHE[source] = df
            south = df[df["资金方向"].astype(str).str.contains("南向", na=False)]
            flow = sum(safe_float(x) or 0 for x in south.get("成交净买额", []))
            base.update({"value": flow, "change_pct": None, "state": "positive" if flow > 0 else "negative" if flow < 0 else "neutral"})
            return base
        if source == "currency_boc_sina":
            df = METRIC_CACHE.get(source)
            if df is None:
                df = ak.currency_boc_sina()
                METRIC_CACHE[source] = df
            records = df.tail(2).to_dict("records")
            if not records:
                return base
            latest = safe_float(records[-1].get("央行中间价"))
            prev = safe_float(records[-2].get("央行中间价")) if len(records) >= 2 else None
            change = (latest / prev - 1) * 100 if latest is not None and prev else None
            base.update({"value": latest, "change_pct": change, "state": metric_state(change)})
            return base
        if source == "futures_global_spot_em":
            df = METRIC_CACHE.get(source)
            if df is None:
                df = ak.futures_global_spot_em()
                METRIC_CACHE[source] = df
            row = match_metric_row(df, spec)
            return metric_from_row(base, row, ["最新价"], ["涨跌幅"])
        if source == "a_share_index_history":
            history = fetch_index_history(
                ak,
                {
                    "name": spec.get("name"),
                    "symbol": spec.get("symbol"),
                    "ak_symbol": spec.get("ak_symbol"),
                },
                notes,
            )
            signal = price_signal(history)
            if signal.get("state") == "data_unavailable":
                return base
            base.update(
                {
                    "value": signal.get("last_close"),
                    "change_pct": signal.get("change_pct"),
                    "state": metric_state(signal.get("change_pct")),
                    "trend": signal.get("trend"),
                }
            )
            return base
    except Exception as exc:
        notes.append(f"context metric {spec.get('name')} via {spec.get('source')} failed: {short_error(exc)}")
    return base


def match_metric_row(df: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
    prefer_code = spec.get("prefer_code")
    if prefer_code and "代码" in df.columns:
        rows = df[df["代码"].astype(str) == str(prefer_code)]
        if not rows.empty:
            return rows.iloc[0].to_dict()
    match = spec.get("match", {})
    column = match.get("column")
    if column in df.columns:
        if "value" in match:
            rows = df[df[column].astype(str) == str(match["value"])]
        elif "contains" in match:
            rows = df[df[column].astype(str).str.contains(str(match["contains"]), na=False)]
        else:
            rows = df
        if not rows.empty:
            rows = rows.dropna(subset=["最新价"]) if "最新价" in rows.columns else rows
            if not rows.empty:
                return rows.iloc[0].to_dict()
    return {}


def metric_from_row(base: Dict[str, Any], row: Dict[str, Any], value_keys: List[str], change_keys: List[str]) -> Dict[str, Any]:
    value = first_float(row, value_keys)
    change = first_float(row, change_keys)
    base.update({"value": value, "change_pct": change, "state": metric_state(change) if value is not None else "unavailable"})
    return base


def evidence_for_holding(
    holding: Dict[str, Any],
    signal: Dict[str, Any],
    context_metrics: Dict[str, Dict[str, Any]],
    metric_config: Dict[str, Any],
) -> Tuple[List[str], List[str]]:
    aliases = metric_config.get("metric_aliases", {})
    manual = set(metric_config.get("manual_review_metrics", []))
    evidence: List[str] = []
    manual_items: List[str] = []
    seen_metric_ids = set()

    if signal.get("state") != "data_unavailable":
        amount_signal = signal.get("amount_signal")
        amount_part = f"，{amount_signal}" if amount_signal and amount_signal != "数据不足" else ""
        evidence.append(
            f"标的量价：{fmt_num(signal.get('last_close'))}，近一日{pct(signal.get('change_pct'))}，{signal.get('trend')}，{signal.get('volume_signal')}{amount_part}"
        )

    for raw_metric in holding.get("watch_metrics", []):
        if raw_metric in {"成交额", "ETF成交额"} and signal.get("amount_signal") != "数据不足":
            continue
        metric_ids = aliases.get(raw_metric, [])
        if metric_ids:
            for metric_id in metric_ids:
                if not metric_id or metric_id in seen_metric_ids:
                    continue
                seen_metric_ids.add(metric_id)
                metric = context_metrics.get(metric_id)
                if metric and metric.get("state") != "unavailable":
                    evidence.append(metric_label(metric))
                else:
                    manual_items.append(f"{raw_metric}：代理指标暂缺")
        elif raw_metric in manual:
            manual_items.append(f"{raw_metric}：需新闻/公告/财报复核")
        else:
            manual_items.append(f"{raw_metric}：暂未配置数据源")
    return evidence[:6], manual_items[:8]


def context_score_for_holding(
    holding: Dict[str, Any],
    context_metrics: Dict[str, Dict[str, Any]],
    metric_config: Dict[str, Any],
) -> Tuple[int, List[str]]:
    aliases = metric_config.get("metric_aliases", {})
    seen_metric_ids = set()
    score = 0
    notes: List[str] = []
    for raw_metric in holding.get("watch_metrics", []):
        for metric_id in aliases.get(raw_metric, []):
            if not metric_id or metric_id in seen_metric_ids:
                continue
            seen_metric_ids.add(metric_id)
            metric = context_metrics.get(metric_id)
            if not metric or metric.get("state") == "unavailable":
                continue
            state = metric.get("state")
            if state == "positive":
                score += 1
            elif state == "negative":
                score -= 1
            notes.append(metric_label(metric))
    return score, notes[:4]


def classify_news(news: List[Dict[str, str]]) -> Tuple[int, List[str]]:
    positive_words = ["回购", "增持", "净流入", "业绩", "超预期", "订单", "中标", "涨超", "提价", "指引", "受益"]
    negative_words = ["风险提示", "限购", "净流出", "下跌", "跌超", "监管", "处罚", "减持", "亏损", "不及预期"]
    score = 0
    notes: List[str] = []
    for item in news[:3]:
        title = item.get("title", "")
        if not title:
            continue
        if any(word in title for word in positive_words):
            score += 1
            notes.append(f"正向新闻：{title}")
        elif any(word in title for word in negative_words):
            score -= 1
            notes.append(f"风险新闻：{title}")
    return score, notes[:3]


def trend_score(signal: Dict[str, Any]) -> Tuple[int, List[str]]:
    if signal.get("state") == "data_unavailable":
        return 0, ["趋势：行情缺失"]
    score = 0
    notes = [f"趋势：{signal.get('trend')}，近一日{pct(signal.get('change_pct'))}"]
    state = signal.get("state")
    if state == "add_watch":
        score += 2
    elif state == "trim_warning":
        score -= 2
    elif state == "stop_review":
        score -= 3

    change = safe_float(signal.get("change_pct"))
    volume = str(signal.get("volume_signal") or "")
    amount = str(signal.get("amount_signal") or "")
    expanded = "明显放量" in volume or "明显放大" in amount
    shrinking = "缩量" in volume or "萎缩" in amount
    if change is not None and expanded:
        if change > 0:
            score += 1
            notes.append("量能：上涨获得成交确认")
        elif change < 0:
            score -= 2
            notes.append("量能：下跌伴随放量，风险权重提高")
    elif change is not None and shrinking:
        if change < 0:
            notes.append("量能：缩量下跌，先观察而非机械减仓")
        elif change > 0:
            notes.append("量能：缩量上涨，追涨确认不足")
    else:
        notes.append(f"量能：{volume or '数据不足'}，{amount or '成交额数据不足'}")
    return score, notes


def decision_for_holding(
    holding: Dict[str, Any],
    signal: Dict[str, Any],
    news: List[Dict[str, str]],
    context_metrics: Dict[str, Dict[str, Any]],
    metric_config: Dict[str, Any],
    portfolio_value: Optional[float],
) -> Dict[str, Any]:
    if signal.get("state") == "data_unavailable":
        return {
            "label": "数据待确认",
            "summary": state_reason(signal, holding),
            "factors": ["行情或关键代理指标缺失，今天不生成交易倾向。"],
            "score": 0,
        }

    shares = safe_float(holding.get("shares")) or 0
    if shares <= 0:
        return {
            "label": "仅观察",
            "summary": "当前持仓为0，只跟踪主题和价格，不输出仓位动作。",
            "factors": [f"标的量价：{fmt_num(signal.get('last_close'))}，近一日{pct(signal.get('change_pct'))}，{signal.get('trend')}"],
            "score": 0,
        }

    score, factors = trend_score(signal)
    context_score, context_notes = context_score_for_holding(holding, context_metrics, metric_config)
    news_score, news_notes = classify_news(news)
    score += context_score + news_score

    pnl = unrealized_pct(holding, signal)
    if pnl is not None:
        factors.append(f"成本：当前价较成本{pct(pnl)}")
        if pnl <= -20 and score < 0:
            score += 1
            factors.append("成本：深度浮亏时默认先复核持仓逻辑，避免把短期弱势机械等同于卖出")
        elif pnl >= 25 and score < 0:
            score -= 1
            factors.append("成本：已有较高浮盈且趋势转弱，止盈保护权重提高")

    market_value = holding_market_value(holding, signal)
    weight = (market_value / portfolio_value * 100) if market_value is not None and portfolio_value else None
    if weight is not None:
        factors.append(f"仓位：估算组合权重{pct(weight)}")
        if weight >= 12 and score < 0:
            score -= 1
            factors.append("仓位：权重较高，风险信号需要更早处理")
        elif weight <= 2 and score < 0:
            score += 1
            factors.append("仓位：权重较低，弱势信号先观察")

    if context_notes:
        factors.append("主题代理：" + "；".join(context_notes))
    else:
        factors.append("主题代理：自动化证据不足，需人工补充行业/宏观判断")
    if news_notes:
        factors.append("新闻事件：" + "；".join(news_notes))

    if score >= 4:
        label = "加仓观察"
        summary = "趋势、量能或主题证据形成较强共振，仍需人工确认仓位和溢价。"
    elif score >= 2:
        label = "偏强观察"
        summary = "有改善信号，但证据还不足以直接升级为明确买入。"
    elif score <= -4:
        label = "风险预警"
        summary = "趋势、量能、主题或仓位风险出现多项负面共振。"
    elif score <= -2:
        label = "复核持仓"
        summary = "弱势信号存在，但需要结合成本、仓位和主题逻辑复核。"
    else:
        label = "持有观察"
        summary = "多空证据尚未形成清晰方向，维持观察和盘中确认。"

    return {"label": label, "summary": summary, "factors": factors[:8], "score": score}


def fetch_index_history(ak: Any, item: Dict[str, Any], notes: List[str]) -> List[Dict[str, Any]]:
    if ak is None:
        return []
    symbol = item.get("ak_symbol") or item.get("symbol")
    attempts = [
        ("stock_zh_index_daily", {"symbol": symbol}),
        ("stock_zh_index_daily_tx", {"symbol": symbol}),
        ("stock_zh_index_daily_em", {"symbol": symbol}),
    ]
    errors = []
    for fn_name, kwargs in attempts:
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            history = normalize_hist_frame(fn(**kwargs))
            if history:
                return history
        except Exception as exc:
            errors.append(f"{item.get('name')} via {fn_name} failed: {short_error(exc)}")
    notes.extend(errors)
    return []


def fetch_holding_history(ak: Any, holding: Dict[str, Any], notes: List[str]) -> List[Dict[str, Any]]:
    if ak is None:
        return []
    code = str(holding.get("code") or "").strip()
    if not code:
        notes.append(f"{holding.get('name')} missing code; skipped price fetch.")
        return []
    market = str(holding.get("market") or "").upper()
    asset_type = str(holding.get("asset_type") or "").lower()
    attempts: List[Tuple[str, Dict[str, Any]]] = []

    if market == "HK":
        attempts.extend(
            [
                ("stock_hk_daily", {"symbol": code}),
                ("stock_hk_hist", {"symbol": code, "period": "daily", "adjust": ""}),
            ]
        )
    elif asset_type in {"etf", "lof"}:
        attempts.extend(
            [
                ("fund_etf_hist_sina", {"symbol": market_prefix(code) + code}),
                ("fund_etf_hist_em", {"symbol": code, "period": "daily", "adjust": ""}),
                ("fund_open_fund_info_em", {"symbol": code, "indicator": "单位净值走势"}),
            ]
        )
    else:
        attempts.extend(
            [
                ("stock_zh_a_hist_tx", {"symbol": market_prefix(code) + code}),
                ("stock_zh_a_hist", {"symbol": code, "period": "daily", "adjust": "qfq"}),
                ("stock_zh_a_daily", {"symbol": market_prefix(code) + code}),
            ]
        )

    errors = []
    for fn_name, kwargs in attempts:
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            history = normalize_hist_frame(fn(**kwargs))
            if history:
                return history
        except Exception as exc:
            errors.append(f"{holding.get('name')} via {fn_name} failed: {short_error(exc)}")
    if asset_type in {"etf", "lof"}:
        snapshot = fetch_fund_spot_snapshot(ak, code, asset_type, notes)
        if snapshot:
            return snapshot
    if market == "CN" and asset_type == "stock":
        snapshot = fetch_a_stock_spot_snapshot(ak, code, notes)
        if snapshot:
            return snapshot
    notes.extend(errors)
    return []


def market_prefix(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "sh"
    return "sz"


def fetch_fund_spot_snapshot(ak: Any, code: str, asset_type: str, notes: List[str]) -> List[Dict[str, Any]]:
    if ak is None:
        return []
    category = "LOF基金" if asset_type == "lof" else "ETF基金"
    try:
        df = SPOT_CACHE.get(category)
        if df is None:
            df = ak.fund_etf_category_sina(symbol=category)
            df["code6"] = df["代码"].astype(str).str[-6:]
            SPOT_CACHE[category] = df
        rows = df[df["code6"] == code]
        if rows.empty:
            notes.append(f"{code} spot fallback not found in {category}.")
            return []
        row = rows.iloc[0].to_dict()
        price = safe_float(row.get("最新价"))
        change_pct = safe_float(row.get("涨跌幅"))
        volume = safe_float(row.get("成交量"))
        amount = safe_float(row.get("成交额"))
        if price is None:
            return []
        prev = price / (1 + change_pct / 100) if change_pct is not None and change_pct != -100 else price
        return [
            {"date": "prev", "close": prev, "volume": None, "amount": None},
            {"date": "spot", "close": price, "volume": volume, "amount": amount},
        ]
    except Exception as exc:
        notes.append(f"{code} spot fallback via fund_etf_category_sina failed: {short_error(exc)}")
        return []


def fetch_a_stock_spot_snapshot(ak: Any, code: str, notes: List[str]) -> List[Dict[str, Any]]:
    if ak is None:
        return []
    try:
        df = SPOT_CACHE.get("A股实时")
        if df is None:
            df = ak.stock_zh_a_spot()
            df["code6"] = df["代码"].astype(str).str[-6:]
            SPOT_CACHE["A股实时"] = df
        rows = df[df["code6"] == code]
        if rows.empty:
            notes.append(f"{code} spot fallback not found in A-share spot list.")
            return []
        row = rows.iloc[0].to_dict()
        price = safe_float(row.get("最新价"))
        change_pct = safe_float(row.get("涨跌幅"))
        volume = safe_float(row.get("成交量"))
        amount = safe_float(row.get("成交额"))
        if price is None:
            return []
        prev = price / (1 + change_pct / 100) if change_pct is not None and change_pct != -100 else price
        return [
            {"date": "prev", "close": prev, "volume": None, "amount": None},
            {"date": "spot", "close": price, "volume": volume, "amount": amount},
        ]
    except Exception as exc:
        notes.append(f"{code} spot fallback via stock_zh_a_spot failed: {short_error(exc)}")
        return []


def fetch_news(ak: Any, holding: Dict[str, Any], notes: List[str], limit: int = 3) -> List[Dict[str, str]]:
    if ak is None:
        return []
    name = str(holding.get("name") or "")
    code = str(holding.get("code") or "")
    if not code and holding.get("needs_review"):
        return []
    candidates = [code, name]
    errors = []
    for query in candidates:
        if not query:
            continue
        for fn_name in ["stock_news_em"]:
            fn = getattr(ak, fn_name, None)
            if fn is None:
                continue
            try:
                df = fn(symbol=query)
                rows = df.head(limit).to_dict("records")
                news = []
                for row in rows:
                    title = first_value(row, ["新闻标题", "标题", "title", "Title"]) or ""
                    url = first_value(row, ["新闻链接", "链接", "url", "URL"]) or ""
                    date = first_value(row, ["发布时间", "时间", "date", "Date"]) or ""
                    if title:
                        news.append({"title": str(title), "url": str(url), "date": str(date)})
                if news:
                    return news
            except Exception as exc:
                errors.append(f"{name} news via {fn_name}({query}) failed: {short_error(exc)}")
    if errors:
        notes.extend(errors)
    return []


def short_error(exc: Exception) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:160] if text else exc.__class__.__name__


def state_label(state: str) -> str:
    return {
        "data_unavailable": "数据待确认",
        "continue_watch": "继续观察",
        "add_watch": "加仓观察",
        "trim_warning": "减仓警戒",
        "stop_review": "止损/止盈复核",
    }.get(state, "继续观察")


def state_reason(signal: Dict[str, Any], holding: Dict[str, Any]) -> str:
    if signal.get("state") == "data_unavailable":
        if holding.get("shares", 0) == 0:
            return "截图显示当前持仓为0，仅保留观察；行情缺失时不生成仓位动作。"
        return "行情或代码缺失，今天只保留风险观察框架，不生成量价判断。"
    trend = signal.get("trend") or "趋势待确认"
    volume = signal.get("volume_signal") or "量能待确认"
    if holding.get("shares", 0) == 0:
        return "截图显示当前持仓为0，仅保留观察，不输出仓位动作。"
    return f"{trend}；{volume}；结合主题标签：{', '.join(holding.get('theme_tags', [])[:4])}。"


def price_line(signal: Dict[str, Any]) -> str:
    if signal.get("state") == "data_unavailable":
        return "行情：数据源不可达或代码未补全，今日不生成量价判断。"
    return (
        f"价格与量价：最新 {fmt_num(signal.get('last_close'))}，近一日 {pct(signal.get('change_pct'))}；"
        f"{signal.get('trend')}；{signal.get('volume_signal')}；{signal.get('amount_signal', '成交额数据不足')}。"
    )


def render_news(news: List[Dict[str, str]]) -> str:
    if not news:
        return "暂无可用新闻源结果；需人工补充公告或主流财经新闻。"
    parts = []
    for item in news:
        title = item.get("title", "").strip()
        url = item.get("url", "").strip()
        date = item.get("date", "").strip()
        if url:
            parts.append(f"{date} [{title}]({url})".strip())
        else:
            parts.append(f"{date} {title}".strip())
    return "；".join(parts)


def data_quality_summary(notes: List[str]) -> Dict[str, Any]:
    unique = dedupe(notes)
    environment = []
    missing_code = []
    network = []
    interface = []
    other = []
    for note in unique:
        if (
            note.startswith("env network dns unavailable:")
            or note.startswith("env network dns detail:")
            or note.startswith("Skipped remote fetch because DNS preflight failed")
        ):
            environment.append(note)
        elif "missing code" in note:
            missing_code.append(note.split(" missing code", 1)[0])
        elif (
            "Could not resolve host" in note
            or "NameResolutionError" in note
            or "Max retries exceeded" in note
            or "RemoteDisconnected" in note
            or "Connection aborted" in note
        ):
            network.append(note)
        elif "unexpected keyword" in note or "unavailable" in note:
            interface.append(note)
        else:
            other.append(note)
    return {
        "total": len(unique),
        "environment": environment,
        "missing_code": missing_code,
        "network": network,
        "interface": interface,
        "other": other,
        "raw": unique,
    }


def render_data_quality(
    notes: List[str],
    today: dt.date,
    index_rows: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    holding_rows: List[Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, str]]]],
) -> List[str]:
    lines: List[str] = ["## 数据质量", ""]
    if not notes:
        lines.append("- 今日未记录到数据源异常。")
        lines.append("")
        return lines

    summary = data_quality_summary(notes)
    index_ok = sum(1 for _, signal in index_rows if signal.get("state") != "data_unavailable")
    holding_price_ok = sum(1 for _, signal, _ in holding_rows if signal.get("state") != "data_unavailable")
    holding_news_ok = sum(1 for _, _, news in holding_rows if news)
    if index_ok or holding_price_ok or holding_news_ok:
        lines.append(
            f"今日报告部分可用：指数行情 {index_ok}/{len(index_rows)}，持仓行情 {holding_price_ok}/{len(holding_rows)}，持仓新闻 {holding_news_ok}/{len(holding_rows)}。"
        )
    else:
        lines.append("今日报告已降级：行情/新闻公开源没有返回可用数据，因此正文只保留持仓框架和人工复核清单。")
    lines.append("")
    lines.append("| 类型 | 数量 | 处理方式 |")
    lines.append("|---|---:|---|")
    lines.append(f"| 运行环境网络/DNS异常 | {1 if summary['environment'] else 0} | 远程抓取已短路；优先修复本机网络、DNS 或代理配置 |")
    lines.append(f"| 主数据源连接失败 | {len(summary['network'])} | 不展示逐条报错；已继续尝试备用源或快照源 |")
    lines.append(f"| 持仓代码待补全 | {len(summary['missing_code'])} | 标的仍保留框架，但不抓取行情 |")
    lines.append(f"| 接口/依赖问题 | {len(summary['interface'])} | 保留在详细日志中，后续按版本修正 |")
    lines.append(f"| 其他问题 | {len(summary['other'])} | 保留在详细日志中 |")
    lines.append("")

    if summary["environment"]:
        lines.append("- 本次运行前已检测到市场数据域名无法解析，说明问题更可能在本机网络、DNS 或代理环境，而不是单一数据接口失效。")
    if summary["missing_code"]:
        sample = "、".join(summary["missing_code"][:16])
        suffix = f"等 {len(summary['missing_code'])} 个" if len(summary["missing_code"]) > 16 else ""
        lines.append(f"- 需要优先补全代码的标的：{sample}{suffix}。")
    if summary["network"]:
        if index_ok == len(index_rows) and holding_price_ok == len(holding_rows):
            lines.append("- 主数据源存在断连，但指数和持仓当前行情已由备用源补齐；部分均线/量能仍依赖历史K线，需以报告正文为准。")
        else:
            lines.append("- 当前仍有部分公开源不可达，不是你的持仓配置失效；自动化下次运行会继续重试。")
    lines.append("- 为避免误导，行情缺失时不会输出涨跌、均线、放量、加减仓倾向。")
    lines.append("")

    lines.append(f"- 详细数据源日志已单独保存：`reports/{today.isoformat()}.data-quality.log`。")
    lines.append("")
    return lines


def build_report() -> Tuple[str, Path]:
    holdings_config = load_structured(CONFIG_DIR / "holdings.yaml")
    sources_config = load_structured(CONFIG_DIR / "sources.yaml")
    metric_config_path = CONFIG_DIR / "metric_sources.yaml"
    metric_config = load_structured(metric_config_path) if metric_config_path.exists() else {}
    today = now_cn().date()
    notes: List[str] = []
    network_ready = network_preflight(notes)
    ak = import_akshare(notes) if network_ready else None
    if not network_ready:
        notes.append("Skipped remote fetch because DNS preflight failed for all configured market-data hosts.")
    context_metrics = fetch_context_metrics(ak, metric_config, notes)

    index_rows = []
    for item in sources_config.get("market_watch", {}).get("a_share_indices", []):
        history = fetch_index_history(ak, item, notes)
        signal = price_signal(history)
        index_rows.append((item, signal))

    holding_rows = []
    for holding in holdings_config.get("holdings", []):
        history = fetch_holding_history(ak, holding, notes)
        signal = price_signal(history)
        news = fetch_news(ak, holding, notes, limit=3)
        holding_rows.append((holding, signal, news))

    report = render_report(today, holdings_config, sources_config, metric_config, context_metrics, index_rows, holding_rows, notes)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{today.isoformat()}.md"
    path.write_text(report, encoding="utf-8")
    if notes:
        log_path = REPORTS_DIR / f"{today.isoformat()}.data-quality.log"
        log_path.write_text("\n".join(dedupe(notes)) + "\n", encoding="utf-8")
    return report, path


def render_report(
    today: dt.date,
    holdings_config: Dict[str, Any],
    sources_config: Dict[str, Any],
    metric_config: Dict[str, Any],
    context_metrics: Dict[str, Dict[str, Any]],
    index_rows: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    holding_rows: List[Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, str]]]],
    notes: List[str],
) -> str:
    generated_at = now_cn().strftime("%Y-%m-%d %H:%M:%S %Z")
    lines: List[str] = []
    lines.append(f"# A股与持仓盘前情报 - {today.isoformat()}")
    lines.append("")
    lines.append(f"- 生成时间：{generated_at}")
    lines.append("- 定位：09:15 盘前准备；仅做条件化风险框架，不构成直接交易指令。")
    lines.append("- 数据原则：免费公开数据源优先；缺失项会明确标注。")
    lines.append("")

    lines.extend(render_data_quality(notes, today, index_rows, holding_rows))

    lines.append("## 隔夜海外与宏观风险")
    lines.append("")
    lines.append("已接入第一批上下文指标：恒生指数/恒生科技、南向资金、美元人民币、黄金/铜/原油、部分A股宽基指数；纳指、标普500、费城半导体、VIX、美债收益率仍作为待接入指标。")
    keywords = sources_config.get("market_watch", {}).get("global_risk_keywords", [])
    lines.append(f"- 今日重点检索关键词：{', '.join(keywords)}")
    lines.append("- 盘前判断方式：若海外科技、美元利率、商品价格与组合主题同向共振，提升对应主题的观察权重；若相互冲突，降低动作强度。")
    lines.append("")

    lines.append("## 上下文指标证据")
    lines.append("")
    available_context = [metric for metric in context_metrics.values() if metric.get("state") != "unavailable"]
    if available_context:
        lines.append("| 指标 | 最新值 | 变化 | 状态 | 来源 |")
        lines.append("|---|---:|---:|---|---|")
        for metric in available_context:
            lines.append(
                f"| {metric.get('name')} | {fmt_num(metric.get('value'))} | {pct(metric.get('change_pct'))} | "
                f"{ {'positive': '偏强', 'negative': '偏弱', 'neutral': '中性'}.get(metric.get('state'), '缺失') } | {metric.get('source')} |"
            )
    else:
        lines.append("- 今日未取得可用的上下文代理指标。")
    lines.append("")

    lines.append("## A股大盘盘前观察")
    lines.append("")
    lines.append("| 指数 | 最新收盘 | 近一日 | MA5 | MA20 | 趋势 |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for item, signal in index_rows:
        lines.append(
            "| {name} | {close} | {change} | {ma5} | {ma20} | {trend} |".format(
                name=item.get("name"),
                close=fmt_num(signal.get("last_close")),
                change=pct(signal.get("change_pct")),
                ma5=fmt_num(signal.get("ma5")),
                ma20=fmt_num(signal.get("ma20")),
                trend=signal.get("trend"),
            )
        )
    lines.append("")
    lines.append("盘前大盘框架：")
    weak_count = sum(1 for _, signal in index_rows if signal.get("state") in {"trim_warning", "stop_review"})
    strong_count = sum(1 for _, signal in index_rows if signal.get("state") == "add_watch")
    unavailable_count = sum(1 for _, signal in index_rows if signal.get("state") == "data_unavailable")
    if unavailable_count == len(index_rows):
        lines.append("- 今日指数行情全部缺失，不生成大盘强弱判断；优先人工查看券商或交易所行情。")
    elif strong_count > weak_count:
        lines.append("- 指数层面偏修复，今日优先观察放量延续和主题扩散。")
    elif weak_count > strong_count:
        lines.append("- 指数层面偏防御，今日优先控制追高和复核高弹性主题风险。")
    else:
        lines.append("- 指数层面信号分化，今日以板块强弱和量能确认作为主判断。")
    lines.append("")

    lines.append("## 持仓标的风险框架")
    lines.append("")
    market_values = [holding_market_value(holding, signal) for holding, signal, _ in holding_rows]
    portfolio_value = sum(value for value in market_values if value is not None) or None
    for holding, signal, news in holding_rows:
        evidence, manual_items = evidence_for_holding(holding, signal, context_metrics, metric_config)
        decision = decision_for_holding(holding, signal, news, context_metrics, metric_config, portfolio_value)
        review = "，代码待确认" if holding.get("needs_review") else ""
        code = holding.get("code") or "待确认"
        lines.append(f"### {holding.get('name')}（{code}，{holding.get('market')}，{holding.get('asset_type')}{review}）")
        lines.append("")
        lines.append(f"- 持仓/成本：{holding.get('shares')} 份/股，成本 {holding.get('cost')} {holding.get('currency', '')}")
        lines.append(f"- 主题标签：{', '.join(holding.get('theme_tags', []))}")
        lines.append(f"- {price_line(signal)}")
        lines.append(f"- 重要新闻：{render_news(news)}")
        lines.append(f"- 观察指标：{'; '.join(holding.get('watch_metrics', []))}")
        lines.append(f"- 指标证据：{'; '.join(evidence) if evidence else '暂无可自动化证据'}")
        lines.append(f"- 人工复核：{'; '.join(manual_items) if manual_items else '无'}")
        lines.append(f"- 风险规则：{'; '.join(holding.get('risk_rules', []))}")
        lines.append(f"- 今日动作框架：**{decision['label']}**。{decision['summary']}（综合分 {decision['score']}）")
        lines.append(f"- 决策依据：{'; '.join(decision['factors'])}")
        lines.append("")

    lines.append("## 今日动作清单")
    lines.append("")
    lines.append("- 开盘前确认：待确认代码的 ETF/LOF 是否已补全，避免错配行情。")
    lines.append("- 开盘后 15-30 分钟确认：组合主题是否放量，避免只凭集合竞价或隔夜情绪动作。")
    lines.append("- 收盘后复盘：把实际强弱与本报告的状态标签对照，迭代 `holdings.yaml` 的观察指标和风险规则。")
    lines.append("")
    lines.append("## 免责声明")
    lines.append("")
    lines.append("本报告由公开数据和预设规则生成，仅用于个人盘前研究和风险管理，不构成证券投资建议。交易决策需结合账户风险承受能力、实时行情和人工复核。")
    lines.append("")
    return "\n".join(lines)


def dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def main() -> int:
    try:
        _, path = build_report()
        print(f"Report written: {path}")
        render_script = ROOT / "scripts" / "render_report_ui.py"
        if render_script.exists():
            result = subprocess.run([sys.executable, str(render_script)], check=False)
            if result.returncode != 0:
                print("UI render failed; Markdown report is still available.", file=sys.stderr)
        return 0
    except Exception:
        print("Failed to generate report.", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
