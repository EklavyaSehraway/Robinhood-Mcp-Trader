"""Robinhood MCP client — connects to the Robinhood Trading MCP server
and exposes typed methods for the operations our engine needs.

Authentication uses OAuth 2.0 with PKCE. On first connection the user must
complete a browser-based login; tokens are persisted and auto-refreshed.

Public methods:
  connect()           — establish session (raises if auth incomplete)
  get_account()       — buying power, account value, account number
  get_positions()     — current holdings with qty/cost/market_value
  get_quote(symbol)   — real-time bid/ask/last for a ticker
  place_order(...)    — submit a limit order, returns order_id
  get_order(order_id) — check order status (filled/pending/cancelled)
  cancel_order(id)    — cancel a pending order
  is_connected()      — True if session is live and authenticated
  preflight_check()   — place a tiny buy+cancel to prove connectivity
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import webbrowser
from pathlib import Path
from dataclasses import dataclass
from threading import Lock
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientMetadata, OAuthToken, OAuthClientInformationFull

log = logging.getLogger("robinhood_mcp")

MCP_URL = "https://agent.robinhood.com/mcp/trading"
STATE_DIR = Path(__file__).parent / "state"
TOKEN_FILE = STATE_DIR / "rh_oauth_tokens.json"
CLIENT_INFO_FILE = STATE_DIR / "rh_client_info.json"

_lock = Lock()


class FileTokenStorage(TokenStorage):
    """Persist OAuth tokens to a local JSON file (gitignored via state/)."""

    async def get_tokens(self) -> OAuthToken | None:
        if TOKEN_FILE.exists():
            try:
                data = json.loads(TOKEN_FILE.read_text())
                return OAuthToken(**data)
            except Exception:
                return None
        return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        TOKEN_FILE.write_text(json.dumps({
            "access_token": tokens.access_token,
            "token_type": tokens.token_type,
            "expires_in": tokens.expires_in,
            "refresh_token": tokens.refresh_token,
            "scope": tokens.scope,
        }))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        if CLIENT_INFO_FILE.exists():
            try:
                data = json.loads(CLIENT_INFO_FILE.read_text())
                return OAuthClientInformationFull(**data)
            except Exception:
                return None
        return None

    async def set_client_info(self, info: OAuthClientInformationFull) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        CLIENT_INFO_FILE.write_text(json.dumps({
            "client_id": info.client_id,
            "client_secret": info.client_secret,
            "redirect_uris": info.redirect_uris,
        }))


def _open_browser(url: str) -> None:
    """Called by the OAuth flow to open the authorization URL."""
    log.info("Opening browser for Robinhood OAuth: %s", url)
    webbrowser.open(url)


@dataclass
class RobinhoodMCP:
    """Manages the MCP connection to Robinhood."""

    _session: ClientSession | None = None
    _connected: bool = False
    _tools: dict[str, Any] = None  # name -> tool schema
    _last_connected_at: float = 0

    def is_connected(self) -> bool:
        return self._connected and self._session is not None

    async def connect(self) -> dict:
        """Establish MCP session. Returns {"connected": True, "tools": [...]}
        or raises on failure."""
        oauth_provider = OAuthClientProvider(
            server_url=MCP_URL,
            client_metadata=OAuthClientMetadata(
                client_name="WeeklySwingTrader",
                redirect_uris=["http://127.0.0.1:8400/callback"],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                scope="trading read",
            ),
            storage=FileTokenStorage(),
            redirect_handler=_open_browser,
            callback_port=8400,
        )

        async with streamablehttp_client(
            url=MCP_URL,
            auth=oauth_provider,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                self._tools = {t.name: t for t in tools_result.tools}
                self._session = session
                self._connected = True
                self._last_connected_at = time.time()
                log.info("Robinhood MCP connected. Tools: %s",
                         list(self._tools.keys()))
                return {
                    "connected": True,
                    "tools": list(self._tools.keys()),
                    "account": await self._call("get_account", {}),
                }

    async def _call(self, tool_name: str, arguments: dict) -> Any:
        """Call an MCP tool and return the parsed result."""
        if not self._session:
            raise RuntimeError("Not connected to Robinhood MCP")
        result = await self._session.call_tool(tool_name, arguments)
        if result.isError:
            raise RuntimeError(f"MCP tool error ({tool_name}): {result.content}")
        # MCP returns content as a list of content blocks
        texts = [c.text for c in result.content if hasattr(c, "text")]
        combined = "\n".join(texts)
        try:
            return json.loads(combined)
        except json.JSONDecodeError:
            return combined

    async def get_account(self) -> dict:
        return await self._call("get_account", {})

    async def get_positions(self) -> list[dict]:
        result = await self._call("get_positions", {})
        return result if isinstance(result, list) else result.get("positions", [])

    async def get_quote(self, symbol: str) -> dict:
        return await self._call("get_quote", {"symbol": symbol})

    async def get_quotes(self, symbols: list[str]) -> dict[str, float]:
        """Get latest prices for multiple symbols. Returns {symbol: price}."""
        prices = {}
        for sym in symbols:
            try:
                q = await self.get_quote(sym)
                px = float(q.get("last_trade_price") or q.get("last") or q.get("price", 0))
                if px > 0:
                    prices[sym] = px
            except Exception as e:
                log.warning("quote failed for %s: %s", sym, e)
        return prices

    async def place_order(
        self,
        symbol: str,
        side: str,       # "buy" | "sell"
        quantity: int,
        limit_price: float,
        time_in_force: str = "gfd",  # good for day
    ) -> dict:
        """Place a limit order. Returns order details including order_id."""
        return await self._call("place_order", {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": "limit",
            "limit_price": round(limit_price, 2),
            "time_in_force": time_in_force,
        })

    async def get_order(self, order_id: str) -> dict:
        return await self._call("get_order", {"order_id": order_id})

    async def cancel_order(self, order_id: str) -> dict:
        return await self._call("cancel_order", {"order_id": order_id})

    async def preflight_check(self) -> dict:
        """Verify we can reach the account and query data.
        Does NOT place a real order — just confirms tool access works."""
        try:
            account = await self.get_account()
            # Try fetching a quote for SPY as a connectivity test
            quote = await self.get_quote("SPY")
            return {
                "success": True,
                "account_number": account.get("account_number", "unknown"),
                "buying_power": account.get("buying_power"),
                "spy_price": quote.get("last_trade_price"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def disconnect(self) -> None:
        self._session = None
        self._connected = False
        self._tools = None


# Singleton instance
robinhood = RobinhoodMCP()
