"""Real-time news headlines per ticker, via Yahoo Finance (free, no key).

yfinance's Ticker.news returns recent stories; the payload shape changed
across versions, so parsing is defensive. Only headlines from the last
NEWS_MAX_AGE_HOURS are kept — stale news is worse than no news.
"""
from __future__ import annotations

import time

import yfinance as yf

NEWS_MAX_AGE_HOURS = 48
MAX_HEADLINES_PER_SYMBOL = 4


def _parse_item(item: dict) -> dict | None:
    content = item.get("content", item)
    title = content.get("title")
    if not title:
        return None

    pub = (
        content.get("pubDate")
        or content.get("displayTime")
        or item.get("providerPublishTime")
    )
    age_hours = None
    if isinstance(pub, (int, float)):
        age_hours = (time.time() - pub) / 3600
    elif isinstance(pub, str):
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            age_hours = (time.time() - dt.timestamp()) / 3600
        except ValueError:
            pass

    if age_hours is not None and age_hours > NEWS_MAX_AGE_HOURS:
        return None

    provider = content.get("provider") or {}
    publisher = (
        provider.get("displayName") if isinstance(provider, dict) else None
    ) or item.get("publisher") or ""
    summary = content.get("summary") or ""

    return {
        "title": title,
        "publisher": publisher,
        "summary": summary[:300],
        "age_hours": round(age_hours, 1) if age_hours is not None else None,
    }


def fetch_news(symbols: list[str]) -> dict[str, list[dict]]:
    """Returns {symbol: [headline, ...]} — empty list when nothing recent."""
    out: dict[str, list[dict]] = {}
    for sym in symbols:
        headlines = []
        try:
            for item in yf.Ticker(sym).news or []:
                parsed = _parse_item(item)
                if parsed:
                    headlines.append(parsed)
                if len(headlines) >= MAX_HEADLINES_PER_SYMBOL:
                    break
        except Exception:
            pass
        out[sym] = headlines
    return out


if __name__ == "__main__":
    news = fetch_news(["AAPL", "SPY"])
    for sym, items in news.items():
        print(f"--- {sym} ({len(items)} headlines)")
        for h in items:
            print(f"  [{h['age_hours']}h] {h['title']} ({h['publisher']})")
