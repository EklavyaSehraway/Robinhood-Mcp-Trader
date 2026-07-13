"""Live trading engine — mirrors paper_trader.py's interface but routes
orders through the Robinhood MCP.

Safety rules:
  - All risk limits (stop %, target %, max position %, 1% risk per trade)
    are computed IDENTICALLY to paper mode before any order is sent.
  - Limit orders only — never market orders.
  - Every order is logged with full context before and after submission.
  - On any MCP/order error, the system logs and skips — never retries blindly.
  - Reconciliation: each manage cycle compares our book against Robinhood's
    actual positions and flags mismatches.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from robinhood_mcp import robinhood

log = logging.getLogger("live_trader")

STATE_DIR = Path(__file__).parent / "state"
LIVE_LOG = STATE_DIR / "live_orders.jsonl"


def _log_order(event: dict) -> None:
    """Append an order event to the audit log."""
    STATE_DIR.mkdir(exist_ok=True)
    with open(LIVE_LOG, "a") as f:
        f.write(json.dumps({**event, "ts": time.time()}) + "\n")


async def open_position_live(
    symbol: str,
    shares: int,
    limit_price: float,
    stop: float,
    target: float,
    setup: str,
    rationale: str,
) -> dict:
    """Place a buy limit order via Robinhood MCP.

    Returns {"success": True, "order": {...}} or {"success": False, "error": "..."}.
    """
    if shares <= 0:
        return {"success": False, "error": "zero shares"}

    _log_order({
        "action": "buy_attempt",
        "symbol": symbol,
        "shares": shares,
        "limit_price": limit_price,
        "stop": stop,
        "target": target,
        "setup": setup,
    })

    try:
        order = await robinhood.place_order(
            symbol=symbol,
            side="buy",
            quantity=shares,
            limit_price=limit_price,
            time_in_force="gfd",
        )
        _log_order({"action": "buy_placed", "symbol": symbol, "order": order})
        log.info("LIVE BUY: %s x%d @ limit $%.2f — order_id=%s",
                 symbol, shares, limit_price, order.get("order_id", "?"))
        return {"success": True, "order": order}
    except Exception as e:
        _log_order({"action": "buy_error", "symbol": symbol, "error": str(e)})
        log.error("LIVE BUY FAILED: %s — %s", symbol, e)
        return {"success": False, "error": str(e)}


async def close_position_live(
    symbol: str,
    shares: int,
    limit_price: float,
    reason: str,
) -> dict:
    """Place a sell limit order via Robinhood MCP.

    limit_price for exits:
      - stop hit: use the current market price (slightly below stop)
      - target hit: use the current market price
      - time/manual: use the current market price
    """
    if shares <= 0:
        return {"success": False, "error": "zero shares"}

    _log_order({
        "action": "sell_attempt",
        "symbol": symbol,
        "shares": shares,
        "limit_price": limit_price,
        "reason": reason,
    })

    try:
        order = await robinhood.place_order(
            symbol=symbol,
            side="sell",
            quantity=shares,
            limit_price=limit_price,
            time_in_force="gfd",
        )
        _log_order({"action": "sell_placed", "symbol": symbol, "order": order, "reason": reason})
        log.info("LIVE SELL: %s x%d @ limit $%.2f (%s) — order_id=%s",
                 symbol, shares, limit_price, reason, order.get("order_id", "?"))
        return {"success": True, "order": order}
    except Exception as e:
        _log_order({"action": "sell_error", "symbol": symbol, "error": str(e)})
        log.error("LIVE SELL FAILED: %s — %s", symbol, e)
        return {"success": False, "error": str(e)}


async def reconcile_positions(our_book: list[dict]) -> dict:
    """Compare our tracked positions against Robinhood's actual holdings.
    Returns mismatches for logging/alerting."""
    try:
        rh_positions = await robinhood.get_positions()
    except Exception as e:
        return {"error": str(e)}

    rh_by_symbol = {}
    for p in rh_positions:
        sym = p.get("symbol", "")
        qty = float(p.get("quantity", 0))
        if qty > 0:
            rh_by_symbol[sym] = qty

    our_by_symbol = {p["symbol"]: p["shares"] for p in our_book}
    mismatches = []

    for sym, our_qty in our_by_symbol.items():
        rh_qty = rh_by_symbol.get(sym, 0)
        if abs(our_qty - rh_qty) > 0.01:
            mismatches.append({
                "symbol": sym,
                "our_qty": our_qty,
                "rh_qty": rh_qty,
                "diff": rh_qty - our_qty,
            })

    for sym, rh_qty in rh_by_symbol.items():
        if sym not in our_by_symbol:
            mismatches.append({
                "symbol": sym,
                "our_qty": 0,
                "rh_qty": rh_qty,
                "diff": rh_qty,
                "note": "in Robinhood but not tracked by engine",
            })

    if mismatches:
        log.warning("Position mismatches: %s", json.dumps(mismatches))
        _log_order({"action": "reconcile_mismatch", "mismatches": mismatches})

    return {"mismatches": mismatches, "rh_positions": len(rh_by_symbol)}
