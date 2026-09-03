import json
from datetime import UTC, datetime, timedelta

import pytest

from meta_trader_ai.market_structure import (
    MarketStructureError,
    detect_structure,
    load_structure_context,
)


def test_detects_bullish_hh_hl_and_bos_up() -> None:
    highs = [
        10, 11, 13, 12, 11, 12, 14, 13, 12, 13,
        15, 14, 13, 14, 16, 15, 14, 15, 17, 17.5,
    ]
    lows = [
        9, 10, 11, 10, 8, 10, 11, 10, 9, 10,
        12, 11, 10, 11, 13, 12, 11, 12, 14, 15,
    ]
    closes = [
        9.5, 10.5, 12, 11, 9, 11, 13, 12, 10, 12,
        14, 13, 11, 13, 15, 14, 12, 14, 15.5, 17.2,
    ]

    result = detect_structure(highs, lows, closes)

    assert result.trend == "BULLISH"
    assert result.sequence == "HH_HL"
    assert result.event == "BOS_UP"
    assert result.last_swing_high == 16
    assert result.last_swing_low == 11


def test_detects_bearish_lh_ll_and_choch_up() -> None:
    highs = [
        20, 19, 18, 19, 20, 18, 17, 18, 19, 17,
        16, 17, 18, 16, 15, 16, 17, 18, 19, 19,
    ]
    lows = [
        19, 18, 17, 18, 16, 17, 16, 17, 15, 16,
        15, 16, 14, 15, 14, 15, 13, 14, 15, 16,
    ]
    closes = [
        19.5, 18.5, 17.5, 18.5, 17, 17.5, 16.5, 17.5, 16, 16.5,
        15.5, 16.5, 15, 15.5, 14.5, 15.5, 14, 17.5, 17.5, 18.5,
    ]

    result = detect_structure(highs, lows, closes)

    assert result.trend == "BEARISH"
    assert result.sequence == "LH_LL"
    assert result.event == "CHOCH_UP"


def test_structure_loader_rejects_stale_context(tmp_path) -> None:
    now = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    payload = {
        "symbol": "XAUUSD_o",
        "generated_at": (now - timedelta(minutes=5)).isoformat(),
        "h1": {"highs": [1.0] * 20, "lows": [0.5] * 20, "closes": [0.8] * 20},
        "h4": {"highs": [1.0] * 20, "lows": [0.5] * 20, "closes": [0.8] * 20},
    }
    path = tmp_path / "mt5_context.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MarketStructureError, match="stale"):
        load_structure_context(
            path,
            symbol="XAUUSD_o",
            max_age_seconds=90,
            now=now,
        )
