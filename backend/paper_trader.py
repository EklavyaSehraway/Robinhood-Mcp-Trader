"""Paper-trading engine.

Simulates the strategy with a virtual cash balance. Positions are
opened from scan recommendations and closed on stop, target, or the
5-trading-day time limit. All state persists to JSON so the app can be
restarted without losing the book.

Sizing: risk-based. Each position risks ~1% of equity between entry
and stop, capped at 25% of equity per position, max 5 open positions.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import numpy as np
import yfinance as yf

STATE_DIR = Path(__file__).parent / "state"
PORTFOLIO_FILE = STATE_DIR / "portfolio.json"

STARTING_CASH = 25000.0
RISK_PER_TRADE = 0.01        # 1% of equity risked per position
MAX_POSITION_PCT = 0.10      # 10 slots on one book -> ~10% each
MAX_POSITIONS = 10
MAX_HOLD_DAYS = 5            # trading days

_lock = Lock()


def _now() -> float:
    return time.time()


def _default_state() -> dict:
    return {
        "cash": STARTING_CASH,
        "starting_cash": STARTING_CASH,
        "mode": "paper",
        "positions": [],      # open positions
        "trades": [],         # closed trades
        "equity_curve": [],   # [{t, equity}]
        "created_at": _now(),
    }


def load_state() -> dict:
    if PORTFOLIO_FILE.exists():
        return json.loads(PORTFOLIO_FILE.read_text())
    return _default_state()


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    tmp = PORTFOLIO_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(PORTFOLIO_FILE)


def get_quotes(symbols: list[str]) -> dict[str, float]:
    """Latest prices via yfinance fast_info; falls back to recent close."""
    prices: dict[str, float] = {}
    if not symbols:
        return prices
    data = yf.download(
        symbols, period="1d", interval="1m", group_by="ticker",
        auto_adjust=True, threads=True, progress=False,
    )
    for sym in symbols:
        try:
            df = data[sym] if len(symbols) > 1 else data
            px = float(df["Close"].dropna().iloc[-1])
            if not np.isnan(px):
                prices[sym] = px
        except Exception:
            continue
    missing = [s for s in symbols if s not in prices]
    if missing:
        daily = yf.download(
            missing, period="5d", interval="1d", group_by="ticker",
            auto_adjust=True, threads=True, progress=False,
        )
        for sym in missing:
            try:
                df = daily[sym] if len(missing) > 1 else daily
                prices[sym] = float(df["Close"].dropna().iloc[-1])
            except Exception:
                continue
    return prices


def equity(state: dict, prices: dict[str, float]) -> float:
    total = state["cash"]
    for pos in state["positions"]:
        px = prices.get(pos["symbol"], pos["entry_price"])
        total += pos["shares"] * px
    return round(total, 2)


def open_positions_from_recs(recs: list[dict], market_ok: bool) -> dict:
    """Open new paper positions from scan recommendations."""
    with _lock:
        state = load_state()
        opened = []
        if not market_ok:
            return {"opened": [], "reason": "market regime filter: SPY below 50-day avg"}

        held = {p["symbol"] for p in state["positions"]}
        # symbols traded in the last 2 days are skipped to avoid churn
        recently_closed = {
            t["symbol"] for t in state["trades"]
            if _now() - t["exit_time"] < 2 * 86400
        }
        prices = get_quotes([r["symbol"] for r in recs if r["symbol"] not in held])
        eq = equity(state, prices)

        for rec in recs:
            if len(state["positions"]) >= MAX_POSITIONS:
                break
            sym = rec["symbol"]
            if sym in held or sym in recently_closed:
                continue
            px = prices.get(sym)
            if px is None or px <= 0:
                continue

            stop_dist = px * rec["stop_pct"] / 100
            risk_dollars = eq * RISK_PER_TRADE
            shares = risk_dollars / stop_dist
            max_shares = (eq * MAX_POSITION_PCT) / px
            # whole shares only — round down; expensive names may get 0 and be skipped
            shares = int(min(shares, max_shares, state["cash"] / px))
            cost = shares * px
            if shares <= 0 or cost < 20 or cost > state["cash"]:
                continue

            pos = {
                "id": str(uuid.uuid4())[:8],
                "symbol": sym,
                "asset_type": rec["asset_type"],
                "setup": rec["setup"],
                "shares": shares,
                "entry_price": round(px, 4),
                "cost": round(cost, 2),
                "stop": round(px - stop_dist, 4),
                "target": round(px + rec["target_pct"] / 100 * px, 4),
                "entry_time": _now(),
                "entry_date": datetime.now(timezone.utc).isoformat(),
                "trading_days_held": 0,
                "rationale": rec["rationale"],
            }
            state["cash"] = round(state["cash"] - cost, 2)
            state["positions"].append(pos)
            held.add(sym)
            opened.append(pos)

        save_state(state)
        return {"opened": opened}


def _close(state: dict, pos: dict, px: float, reason: str) -> dict:
    proceeds = pos["shares"] * px
    pnl = proceeds - pos["cost"]
    trade = {
        **pos,
        "exit_price": round(px, 4),
        "exit_time": _now(),
        "exit_date": datetime.now(timezone.utc).isoformat(),
        "exit_reason": reason,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / pos["cost"] * 100, 2),
    }
    state["cash"] = round(state["cash"] + proceeds, 2)
    state["positions"] = [p for p in state["positions"] if p["id"] != pos["id"]]
    state["trades"].append(trade)
    return trade


def manage_positions(trading_date: str | None = None) -> dict:
    """Check stops/targets/time exits against live prices.

    trading_date (YYYY-MM-DD, ET) increments each position's hold-day counter
    at most once per session — persisted, so restarts don't double-count.
    """
    with _lock:
        state = load_state()
        new_day = trading_date is not None and state.get("last_day_counted") != trading_date
        if new_day:
            state["last_day_counted"] = trading_date
        if not state["positions"]:
            prices = {}
        else:
            prices = get_quotes([p["symbol"] for p in state["positions"]])
        closed = []

        for pos in list(state["positions"]):
            px = prices.get(pos["symbol"])
            if px is None:
                continue
            if new_day:
                pos["trading_days_held"] += 1

            if px <= pos["stop"]:
                closed.append(_close(state, pos, px, "stop"))
            elif px >= pos["target"]:
                closed.append(_close(state, pos, px, "target"))
            elif pos["trading_days_held"] >= MAX_HOLD_DAYS:
                closed.append(_close(state, pos, px, "time_limit"))

        eq = equity(state, prices)
        state["equity_curve"].append({"t": _now(), "equity": eq})
        # keep the curve bounded: one point per ~10 min, capped at 5000 points
        if len(state["equity_curve"]) > 5000:
            state["equity_curve"] = state["equity_curve"][-5000:]

        save_state(state)
        return {"closed": closed, "equity": eq, "prices": prices}


def close_position(position_id: str) -> dict:
    """Manually close one open position at the current market price."""
    with _lock:
        state = load_state()
        pos = next((p for p in state["positions"] if p["id"] == position_id), None)
        if pos is None:
            return {"error": f"position {position_id} not found"}
        prices = get_quotes([pos["symbol"]])
        px = prices.get(pos["symbol"])
        if px is None or px <= 0:
            return {"error": f"no market price available for {pos['symbol']}"}
        trade = _close(state, pos, px, "manual")
        save_state(state)
        return {"closed": trade}


def portfolio_snapshot() -> dict:
    state = load_state()
    prices = get_quotes([p["symbol"] for p in state["positions"]]) if state["positions"] else {}
    eq = equity(state, prices)
    positions = []
    for pos in state["positions"]:
        px = prices.get(pos["symbol"], pos["entry_price"])
        mv = pos["shares"] * px
        positions.append({
            **pos,
            "current_price": round(px, 4),
            "market_value": round(mv, 2),
            "unrealized_pnl": round(mv - pos["cost"], 2),
            "unrealized_pnl_pct": round((mv - pos["cost"]) / pos["cost"] * 100, 2),
        })
    trades = state["trades"]
    wins = [t for t in trades if t["pnl"] > 0]
    return {
        "mode": state["mode"],
        "cash": state["cash"],
        "equity": eq,
        "starting_cash": state["starting_cash"],
        "total_return_pct": round((eq / state["starting_cash"] - 1) * 100, 2),
        "positions": positions,
        "trades": sorted(trades, key=lambda t: t["exit_time"], reverse=True),
        "stats": {
            "n_trades": len(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
            "total_pnl": round(sum(t["pnl"] for t in trades), 2),
            "avg_pnl_pct": round(float(np.mean([t["pnl_pct"] for t in trades])), 2) if trades else None,
        },
        "equity_curve": state["equity_curve"],
    }


def reset_portfolio() -> dict:
    with _lock:
        state = _default_state()
        save_state(state)
        return state
