"""FastAPI server + background scheduler for the weekly-swing paper trader.

Endpoints (all JSON):
  GET  /api/status      — engine status, market hours, last scan time
  GET  /api/portfolio   — cash, equity, positions, closed trades, stats
  GET  /api/scan        — latest scan recommendations
  POST /api/scan/run    — force a scan now
  POST /api/reset       — reset the paper portfolio to $1,000

A background loop scans every 30 minutes during market hours, manages
open positions (stops/targets/time exits) every 5 minutes, and enters
new positions from fresh scans. Off-hours it idles.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import ai_analyst
import paper_trader
import live_trader
import strategy
from robinhood_mcp import robinhood
from universe import get_universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)-14s │ %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet down noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
log = logging.getLogger("trader")

SCAN_INTERVAL = 10 * 60
MANAGE_INTERVAL = 5 * 60       # stop/target/time-exit checks
PRICE_REFRESH_INTERVAL = 30    # refresh cached quotes every 30s

app = FastAPI(title="Weekly Swing Paper Trader")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_engine_state = {
    "last_scan_at": None,
    "last_manage_at": None,
    "last_price_refresh_at": None,
    "running": True,
    "errors": [],
}


def _et_now() -> datetime:
    """US Eastern time without external tz deps (handles DST approximately:
    DST runs March–November; good enough for market-hours gating)."""
    utc = datetime.now(timezone.utc)
    offset = 4 if 3 <= utc.month <= 11 else 5
    return utc - timedelta(hours=offset)


def market_open() -> bool:
    et = _et_now()
    if et.weekday() >= 5:
        return False
    minutes = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


AI_REVIEW_TTL = 30 * 60  # re-run the AI at most every 30 min for the same names


def run_scan_with_ai() -> dict:
    """Quant scan, then AI news review over the candidates. The AI annotates
    each rec (endorse/neutral/veto) and the merged result is what the
    dashboard shows and the trader consumes.

    The quant scan runs every cycle; the Bedrock review is reused when the
    candidate set hasn't changed and the last review is fresh — new names
    always trigger a fresh review.
    """
    prev = strategy.load_last_scan() or {}  # before run_scan overwrites the file
    scan = strategy.run_scan()
    symbols = sorted(r["symbol"] for r in scan["recommendations"])

    prev_symbols = sorted(r["symbol"] for r in prev.get("recommendations", []))
    prev_review_at = prev.get("ai_reviewed_at", 0)
    reuse = (
        prev.get("ai_reviewed")
        and symbols == prev_symbols
        and time.time() - prev_review_at < AI_REVIEW_TTL
    )

    if reuse:
        prev_ai = {r["symbol"]: r.get("ai") for r in prev["recommendations"]}
        for r in scan["recommendations"]:
            r["ai"] = prev_ai.get(r["symbol"])
            if r["ai"] and r["ai"]["verdict"] == "endorse":
                r["score"] = round(r["score"] + 5, 1)
        scan["recommendations"].sort(key=lambda r: r["score"], reverse=True)
        scan["ai_reviewed"] = True
        scan["ai_reviewed_at"] = prev_review_at
        scan["ai_market_note"] = prev.get("ai_market_note", "")
        scan["news"] = prev.get("news", {})
    else:
        review = ai_analyst.review_candidates(
            scan["recommendations"], scan.get("spy_1m", 0.0)
        )
        scan["recommendations"] = ai_analyst.apply_verdicts(
            scan["recommendations"], review
        )
        scan["ai_reviewed"] = review.get("reviewed", False)
        scan["ai_reviewed_at"] = time.time() if scan["ai_reviewed"] else 0
        scan["ai_market_note"] = review.get("market_note", "")
        scan["news"] = review.get("news", {})

    strategy.save_scan(scan)
    return scan


async def scheduler():
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("scheduler started — scan every %ds, manage every %ds, prices every %ds",
             SCAN_INTERVAL, MANAGE_INTERVAL, PRICE_REFRESH_INTERVAL)
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    while _engine_state["running"]:
        try:
            if market_open():
                now = time.time()
                et_date = _et_now().strftime("%Y-%m-%d")

                last_manage = _engine_state["last_manage_at"] or 0
                if now - last_manage >= MANAGE_INTERVAL:
                    result = await asyncio.to_thread(
                        paper_trader.manage_positions, et_date
                    )
                    _engine_state["last_manage_at"] = now
                    for t in result["closed"]:
                        log.info("closed %s (%s) pnl=%.2f", t["symbol"],
                                 t["exit_reason"], t["pnl"])

                last_scan = _engine_state["last_scan_at"] or 0
                if now - last_scan >= SCAN_INTERVAL:
                    scan = await asyncio.to_thread(run_scan_with_ai)
                    _engine_state["last_scan_at"] = now
                    log.info("scan done: %d scanned, %d recs, market_ok=%s, ai=%s",
                             scan["scanned"], len(scan["recommendations"]),
                             scan["market_ok"], scan.get("ai_reviewed"))
                    tradeable = [
                        r for r in scan["recommendations"]
                        if not (r.get("ai") and r["ai"]["verdict"] == "veto")
                    ]
                    opened = await asyncio.to_thread(
                        paper_trader.open_positions_from_recs,
                        tradeable, scan["market_ok"],
                    )
                    for p in opened.get("opened", []):
                        log.info("opened %s x%.4f @ %.2f", p["symbol"],
                                 p["shares"], p["entry_price"])
        except Exception as e:
            log.exception("scheduler error")
            _engine_state["errors"] = (_engine_state["errors"] + [str(e)])[-10:]
        await asyncio.sleep(30)


async def price_loop():
    """Dedicated loop for 30s price refresh — never blocked by scans."""
    log.info("price refresh loop started (every %ds)", PRICE_REFRESH_INTERVAL)
    while _engine_state["running"]:
        try:
            await asyncio.to_thread(paper_trader.refresh_prices)
            _engine_state["last_price_refresh_at"] = time.time()
            log.info("prices refreshed (%d positions)",
                     len(paper_trader.load_state()["positions"]))
        except Exception as e:
            log.warning("price refresh error: %s", e)
            _engine_state["last_price_refresh_at"] = time.time()
        await asyncio.sleep(PRICE_REFRESH_INTERVAL)


@app.on_event("startup")
async def startup():
    # Warm the scan cache on boot if empty so the dashboard has data.
    if strategy.load_last_scan() is None:
        asyncio.create_task(asyncio.to_thread(run_scan_with_ai))
    asyncio.create_task(price_loop())
    asyncio.create_task(scheduler())


@app.get("/api/status")
def status():
    return {
        "market_open": market_open(),
        "et_time": _et_now().isoformat(),
        "last_scan_at": _engine_state["last_scan_at"],
        "last_manage_at": _engine_state["last_manage_at"],
        "last_price_refresh_at": _engine_state["last_price_refresh_at"],
        "price_refresh_interval": PRICE_REFRESH_INTERVAL,
        "mode": paper_trader.load_state().get("mode", "paper"),
        "errors": _engine_state["errors"],
    }


@app.get("/api/portfolio")
async def portfolio():
    snap = paper_trader.portfolio_snapshot(use_cached_prices=True)
    # If any position has no cached price yet, trigger a background refresh
    if any(p.get("current_price") == p.get("entry_price") for p in snap.get("positions", [])):
        asyncio.ensure_future(asyncio.to_thread(paper_trader.refresh_prices))
    return snap


@app.get("/api/scan")
def scan():
    return strategy.load_last_scan() or {"recommendations": [], "timestamp": None}


@app.post("/api/scan/run")
async def scan_run():
    result = await asyncio.to_thread(run_scan_with_ai)
    _engine_state["last_scan_at"] = time.time()
    return result


@app.post("/api/positions/{position_id}/close")
async def close_position(position_id: str):
    result = await asyncio.to_thread(paper_trader.close_position, position_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    log.info("manual close: %s pnl=%.2f", result["closed"]["symbol"],
             result["closed"]["pnl"])
    return result


@app.post("/api/mode")
async def set_mode(body: dict):
    mode = body.get("mode", "paper")
    if mode not in ("paper", "live"):
        raise HTTPException(status_code=400, detail="mode must be 'paper' or 'live'")
    if mode == "live" and not robinhood.is_connected():
        raise HTTPException(
            status_code=400,
            detail="Cannot switch to live: Robinhood MCP is not connected. "
                   "Use the Setup Guide to connect first.",
        )
    state = paper_trader.load_state()
    state["mode"] = mode
    paper_trader.save_state(state)
    log.info("trading mode set to: %s", mode)
    return {"mode": mode, "connected": robinhood.is_connected()}


@app.get("/api/mode")
def get_mode():
    state = paper_trader.load_state()
    return {
        "mode": state.get("mode", "paper"),
        "connected": robinhood.is_connected(),
    }


@app.post("/api/robinhood/connect")
async def rh_connect():
    """Initiate Robinhood MCP OAuth connection. Will open a browser for login."""
    try:
        result = await robinhood.connect()
        return result
    except Exception as e:
        log.exception("Robinhood MCP connection failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/robinhood/status")
async def rh_status():
    """Check if Robinhood MCP is connected and functional."""
    if not robinhood.is_connected():
        return {"connected": False}
    try:
        check = await robinhood.preflight_check()
        return {"connected": True, **check}
    except Exception as e:
        return {"connected": False, "error": str(e)}


@app.post("/api/robinhood/preflight")
async def rh_preflight():
    """Run a preflight check: verify account access and quote retrieval.
    This proves orders CAN be sent before live mode is unlocked."""
    if not robinhood.is_connected():
        raise HTTPException(status_code=400, detail="Not connected to Robinhood MCP")
    result = await robinhood.preflight_check()
    return result


@app.post("/api/robinhood/disconnect")
async def rh_disconnect():
    """Disconnect from Robinhood MCP and revert to paper mode."""
    await robinhood.disconnect()
    state = paper_trader.load_state()
    state["mode"] = "paper"
    paper_trader.save_state(state)
    return {"connected": False, "mode": "paper"}


@app.post("/api/settings/keys")
async def save_keys(body: dict):
    """Save AWS Bedrock keys to config.py (local testing convenience).
    In production these should come from env vars or a secrets manager."""
    access_key = body.get("aws_access_key_id", "").strip()
    secret_key = body.get("aws_secret_access_key", "").strip()
    if not access_key or not secret_key:
        raise HTTPException(status_code=400, detail="both keys are required")
    config_path = Path(__file__).parent / "config.py"
    config_path.write_text(f'''"""Local-testing config — auto-saved from dashboard Settings."""

AWS_ACCESS_KEY_ID = "{access_key}"
AWS_SECRET_ACCESS_KEY = "{secret_key}"
AWS_REGION = "us-east-1"

BEDROCK_MODEL = "anthropic.claude-opus-4-8"
''')
    # Reload the config module so the AI analyst picks up new keys
    import importlib
    importlib.reload(config)
    log.info("AWS keys updated via dashboard settings")
    return {"saved": True}


@app.post("/api/reset")
def reset():
    return paper_trader.reset_portfolio()


@app.get("/api/universe")
def universe():
    return get_universe()


# Serve the React dashboard. We mount at "/ui" for static assets, and
# manually serve index.html for "/" so API routes aren't shadowed.
dist = Path(__file__).parent.parent / "dashboard" / "dist"
if dist.exists():
    from starlette.responses import FileResponse

    # Static assets (JS/CSS bundles)
    app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

    @app.get("/")
    async def serve_index():
        return FileResponse(str(dist / "index.html"))

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        # If it's not an API route and the file exists, serve it; else index.html
        file = dist / path
        if file.exists() and file.is_file():
            return FileResponse(str(file))
        return FileResponse(str(dist / "index.html"))
