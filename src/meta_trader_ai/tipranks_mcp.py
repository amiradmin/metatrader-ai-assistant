"""Official TipRanks MCP client used to refresh local higher-timeframe context."""

import json
from datetime import datetime, timezone
from urllib.parse import quote

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from meta_trader_ai.models import TipRanksContext
from meta_trader_ai.tipranks import normalize_symbol


class TipRanksMcpError(RuntimeError):
    """Raised when the remote TipRanks MCP request cannot be converted to context."""


def _first_quote(payload: object) -> dict[str, object]:
    """Extract one quote row from the shapes returned by the MCP tool."""
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    if isinstance(payload, dict):
        for key in ("data", "quotes", "result"):
            value = payload.get(key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0]
        if "symbol" in payload and "price" in payload:
            return payload
    raise TipRanksMcpError("TipRanks MCP returned an unsupported quote payload")


def _result_payload(result: object) -> object:
    """Prefer structured MCP content and fall back to JSON text content."""
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured

    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise TipRanksMcpError("TipRanks MCP response did not contain JSON quote data")


def quote_to_context(payload: object, now: datetime | None = None) -> TipRanksContext:
    """Convert a get_forex_quote response into the local context model."""
    row = _first_quote(payload)
    try:
        symbol = str(row["symbol"])
        price = float(row["price"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TipRanksMcpError("TipRanks quote is missing symbol or price") from exc

    def optional_float(*keys: str) -> float | None:
        for key in keys:
            value = row.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
        return None

    return TipRanksContext(
        symbol=symbol,
        price=price,
        change_percentage=optional_float("changePercentage", "change_percentage"),
        price_avg_50=optional_float("priceAvg50", "price_avg_50"),
        price_avg_200=optional_float("priceAvg200", "price_avg_200"),
        updated_at=now or datetime.now(timezone.utc),
        source="TipRanks MCP",
    )


async def fetch_forex_context(
    symbol: str,
    api_key: str,
    mcp_url: str,
) -> TipRanksContext:
    """Fetch a fresh forex/metal quote from TipRanks' official MCP endpoint."""
    normalized = normalize_symbol(symbol)
    if len(normalized) != 6:
        raise TipRanksMcpError(f"Unsupported TipRanks forex symbol: {symbol}")
    if not api_key.strip():
        raise TipRanksMcpError("TIPRANKS_MCP_API_KEY is not configured")

    separator = "&" if "?" in mcp_url else "?"
    endpoint = f"{mcp_url}{separator}apikey={quote(api_key.strip(), safe='')}"

    try:
        async with streamable_http_client(endpoint) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "get_forex_quote",
                    arguments={"symbol": normalized},
                )
    except Exception as exc:
        raise TipRanksMcpError(f"TipRanks MCP request failed: {exc}") from exc

    return quote_to_context(_result_payload(result))
