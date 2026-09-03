from datetime import datetime, timedelta, timezone

from meta_trader_ai.models import TipRanksContext
from meta_trader_ai.tipranks import load_context, normalize_symbol, save_context
from meta_trader_ai.tipranks_mcp import quote_to_context


def test_normalize_symbol_removes_common_broker_suffix() -> None:
    assert normalize_symbol("EURUSD_o") == "EURUSD"
    assert normalize_symbol("XAUUSD.a") == "XAUUSD"


def test_context_loader_accepts_fresh_matching_symbol(tmp_path) -> None:
    now = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
    path = tmp_path / "tipranks.json"
    context = TipRanksContext(
        symbol="EURUSD",
        price=1.16,
        change_percentage=0.2,
        price_avg_50=1.15,
        price_avg_200=1.14,
        updated_at=now,
    )
    save_context(path, context)

    loaded = load_context(path, "EURUSD_o", max_age_minutes=30, now=now)
    assert loaded is not None
    assert loaded.symbol == "EURUSD"


def test_context_loader_ignores_stale_or_wrong_symbol(tmp_path) -> None:
    now = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
    path = tmp_path / "tipranks.json"
    context = TipRanksContext(
        symbol="EURUSD",
        price=1.16,
        updated_at=now - timedelta(minutes=31),
    )
    save_context(path, context)

    assert load_context(path, "EURUSD", max_age_minutes=30, now=now) is None

    context.updated_at = now
    save_context(path, context)
    assert load_context(path, "GBPUSD", max_age_minutes=30, now=now) is None


def test_mcp_quote_is_converted_to_local_context() -> None:
    now = datetime(2026, 9, 3, 7, 40, tzinfo=timezone.utc)
    payload = [
        {
            "symbol": "XAUUSD",
            "price": 4424.78,
            "changePercentage": 0.83314,
            "priceAvg50": 4253.1,
            "priceAvg200": 4573.1,
        }
    ]

    context = quote_to_context(payload, now=now)

    assert context.symbol == "XAUUSD"
    assert context.price == 4424.78
    assert context.change_percentage == 0.83314
    assert context.price_avg_50 == 4253.1
    assert context.price_avg_200 == 4573.1
    assert context.updated_at == now
    assert context.source == "TipRanks MCP"
