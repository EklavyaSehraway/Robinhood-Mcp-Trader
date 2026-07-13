"""Weekly-swing strategy engine.

Scans the universe (S&P 500 + top ETFs) and produces ranked buy
recommendations designed for a max 5-trading-day holding period.

Two entry setups, both requiring an established uptrend:
  1. PULLBACK  — short-term oversold dip (RSI-2) inside an uptrend.
  2. BREAKOUT  — close near 20-day high with volume expansion.

Every recommendation carries an ATR-based stop and target so risk is
defined before entry. Reward:risk is fixed at 1.6.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from universe import get_universe

STATE_DIR = Path(__file__).parent / "state"
SCAN_FILE = STATE_DIR / "last_scan.json"

MAX_HOLD_DAYS = 5          # trading days
REWARD_RISK = 1.6
MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 10e6   # 20-day average
STOP_PCT_FLOOR = 0.02      # stop never tighter than 2%
STOP_PCT_CAP = 0.05        # stop never wider than 5% (max loss per trade)
TARGET_PCT_CAP = 0.05      # take profit at +5% even if 1.6R sits higher


@dataclass
class Recommendation:
    symbol: str
    asset_type: str          # "stock" | "etf"
    setup: str               # "pullback" | "breakout"
    score: float             # 0-100 composite rank score
    price: float             # last close at scan time
    stop: float
    target: float
    stop_pct: float
    target_pct: float
    mom_12w: float           # 12-week return %
    mom_1m: float            # 1-month return %
    rsi2: float
    rel_strength: float      # 1-month return minus SPY's, %
    avg_dollar_vol: float
    rationale: str


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def _download(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Batch-download 6 months of daily bars; returns per-symbol OHLCV frames."""
    frames: dict[str, pd.DataFrame] = {}
    chunk_size = 150
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]
        data = yf.download(
            chunk, period="6mo", interval="1d", group_by="ticker",
            auto_adjust=True, threads=True, progress=False,
        )
        if data is None or data.empty:
            continue
        for sym in chunk:
            try:
                df = data[sym].dropna(how="all") if len(chunk) > 1 else data.dropna(how="all")
            except KeyError:
                continue
            if len(df) >= 60:
                frames[sym] = df
    return frames


def _evaluate(sym: str, df: pd.DataFrame, asset_type: str, spy_1m: float):
    close = df["Close"]
    price = float(close.iloc[-1])
    if price < MIN_PRICE or math.isnan(price):
        return None

    dollar_vol = float((close * df["Volume"]).rolling(20).mean().iloc[-1])
    if math.isnan(dollar_vol) or dollar_vol < MIN_DOLLAR_VOLUME:
        return None

    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])
    mom_12w = float(close.iloc[-1] / close.iloc[-61] - 1) * 100 if len(close) > 61 else 0.0
    mom_1m = float(close.iloc[-1] / close.iloc[-22] - 1) * 100 if len(close) > 22 else 0.0
    rsi2 = float(_rsi(close, 2).iloc[-1])
    high20 = float(df["High"].rolling(20).max().iloc[-2])  # exclude today
    vol_ratio = float(
        df["Volume"].rolling(5).mean().iloc[-1] / df["Volume"].rolling(20).mean().iloc[-1]
    )
    atr = float(_atr(df).iloc[-1])
    if any(math.isnan(x) for x in (sma20, sma50, rsi2, atr, vol_ratio)):
        return None

    in_uptrend = price > sma50 and mom_12w > 0
    if not in_uptrend:
        return None

    setup = None
    if rsi2 < 15 and mom_12w > 5:
        setup = "pullback"
    elif price >= 0.98 * high20 and sma20 > sma50 and mom_1m > 0 and vol_ratio > 1.05:
        setup = "breakout"
    if setup is None:
        return None

    rel_strength = mom_1m - spy_1m

    # Composite score: momentum quality + setup-specific edge, scaled ~0-100.
    score = (
        min(mom_12w, 40) * 0.8
        + min(max(rel_strength, -10), 15) * 1.5
        + (15 - min(rsi2, 15)) * 1.2 * (setup == "pullback")
        + min(max((vol_ratio - 1) * 40, 0), 12) * (setup == "breakout")
        + 10 * (price > sma20)
    )

    stop_dist = min(max(1.5 * atr, STOP_PCT_FLOOR * price), STOP_PCT_CAP * price)
    target_dist = min(REWARD_RISK * stop_dist, TARGET_PCT_CAP * price)
    stop = round(price - stop_dist, 2)
    target = round(price + target_dist, 2)

    if setup == "pullback":
        rationale = (
            f"Uptrend intact (+{mom_12w:.0f}% over 12w, price above 50-day avg) with a "
            f"short-term oversold dip (RSI-2 = {rsi2:.0f}) — buying the dip in strength."
        )
    else:
        rationale = (
            f"Breaking out near its 20-day high on {vol_ratio:.1f}x volume with "
            f"+{mom_1m:.1f}% 1-month momentum ({rel_strength:+.1f}% vs SPY)."
        )

    return Recommendation(
        symbol=sym, asset_type=asset_type, setup=setup, score=round(score, 1),
        price=round(price, 2), stop=stop, target=target,
        stop_pct=round(stop_dist / price * 100, 2),
        target_pct=round(target_dist / price * 100, 2),
        mom_12w=round(mom_12w, 1), mom_1m=round(mom_1m, 1), rsi2=round(rsi2, 1),
        rel_strength=round(rel_strength, 1),
        avg_dollar_vol=round(dollar_vol / 1e6, 1), rationale=rationale,
    )


def run_scan(top_n: int = 15) -> dict:
    universe = get_universe()
    stocks, etfs = universe["stocks"], universe["etfs"]
    frames = _download(sorted(set(stocks + etfs + ["SPY"])))

    spy = frames.get("SPY")
    spy_1m = (
        float(spy["Close"].iloc[-1] / spy["Close"].iloc[-22] - 1) * 100
        if spy is not None and len(spy) > 22 else 0.0
    )
    # Regime filter: only take new longs when SPY is above its 50-day average.
    market_ok = bool(
        spy is not None
        and float(spy["Close"].iloc[-1]) > float(spy["Close"].rolling(50).mean().iloc[-1])
    )

    recs = []
    for sym, df in frames.items():
        asset_type = "etf" if sym in set(etfs) else "stock"
        try:
            rec = _evaluate(sym, df, asset_type, spy_1m)
        except Exception:
            continue
        if rec:
            recs.append(rec)

    recs.sort(key=lambda r: r.score, reverse=True)
    result = {
        "scanned": len(frames),
        "market_ok": market_ok,
        "spy_1m": round(spy_1m, 2),
        "recommendations": [asdict(r) for r in recs[:top_n]],
        "timestamp": time.time(),
    }
    save_scan(result)
    return result


def save_scan(result: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    SCAN_FILE.write_text(json.dumps(result, indent=2))


def load_last_scan() -> dict | None:
    if SCAN_FILE.exists():
        return json.loads(SCAN_FILE.read_text())
    return None


if __name__ == "__main__":
    out = run_scan()
    print(f"scanned={out['scanned']} market_ok={out['market_ok']}")
    for r in out["recommendations"]:
        print(f"{r['symbol']:6s} {r['setup']:9s} score={r['score']:6.1f} "
              f"price={r['price']:8.2f} stop={r['stop']:8.2f} tgt={r['target']:8.2f}")
