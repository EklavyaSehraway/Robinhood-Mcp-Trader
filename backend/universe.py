"""Trading universe: S&P 500 constituents + top ~100 liquid ETFs.

The S&P 500 list is scraped from Wikipedia and cached for 7 days.
ETFs are a curated list of the largest, most liquid index/sector funds —
no leveraged, no inverse, no exotic products.
"""
import io
import json
import time
from pathlib import Path

import pandas as pd
import requests

STATE_DIR = Path(__file__).parent / "state"
CACHE_FILE = STATE_DIR / "universe_cache.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600

# Names that are S&P 500 members but excluded per user's "no meme stocks" rule,
# plus anything with a history of retail-driven squeeze volatility.
DENYLIST = {"GME", "AMC", "BBBY", "DJT", "SMCI"}

# Top ~100 ETFs by AUM/liquidity: broad index, sector, bond, commodity, factor.
# Deliberately excludes leveraged (TQQQ, SQQQ...) and inverse products.
TOP_ETFS = [
    # Broad US market / large cap
    "SPY", "IVV", "VOO", "VTI", "QQQ", "QQQM", "DIA", "RSP", "SPLG", "ITOT",
    "SCHX", "SCHB", "VV", "MGC", "OEF",
    # Mid / small cap
    "IJH", "IJR", "IWM", "VB", "VO", "MDY", "SCHA", "SCHM", "VXF",
    # Growth / value / factor
    "VUG", "VTV", "IWF", "IWD", "SCHD", "VIG", "DGRO", "MTUM", "QUAL", "USMV",
    "VYM", "HDV", "DVY", "SDY", "NOBL", "MOAT", "COWZ",
    # Sectors (SPDR + Vanguard)
    "XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE",
    "XLC", "VGT", "VHT", "VFH", "VDE", "VIS", "VCR", "VDC", "VPU", "VAW",
    "VOX", "VNQ", "SMH", "SOXX", "IGV", "XBI", "IBB", "ITA", "XOP", "KRE",
    "KBE", "IYR", "GDX",
    # International developed / emerging
    "VEA", "IEFA", "EFA", "VWO", "IEMG", "EEM", "VXUS", "IXUS", "ACWI", "VT",
    "EWJ", "INDA", "FXI", "EWZ", "EZU", "VGK",
    # Bonds
    "BND", "AGG", "TLT", "IEF", "SHY", "LQD", "HYG", "TIP", "MUB", "BNDX",
    "VCIT", "VCSH", "GOVT", "SGOV", "BIL",
    # Commodities / alternatives
    "GLD", "IAU", "SLV", "GLDM", "PDBC", "USO",
]


def _fetch_sp500() -> list[str]:
    resp = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    symbols = df["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
    return sorted(set(symbols) - DENYLIST)


def get_universe(force_refresh: bool = False) -> dict:
    """Returns {"stocks": [...], "etfs": [...], "updated_at": epoch}."""
    if not force_refresh and CACHE_FILE.exists():
        cached = json.loads(CACHE_FILE.read_text())
        if time.time() - cached.get("updated_at", 0) < CACHE_TTL_SECONDS:
            return cached

    try:
        stocks = _fetch_sp500()
    except Exception:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text())
        raise

    universe = {
        "stocks": stocks,
        "etfs": sorted(set(TOP_ETFS)),
        "updated_at": time.time(),
    }
    STATE_DIR.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(universe))
    return universe


if __name__ == "__main__":
    u = get_universe(force_refresh=True)
    print(f"{len(u['stocks'])} stocks, {len(u['etfs'])} ETFs")
