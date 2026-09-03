import csv
import io
import json
from datetime import UTC, datetime

import pytest

from meta_trader_ai.research_context import (
    parse_cftc_gold_disaggregated,
    parse_forex_factory_json,
    parse_fred_usd_proxy_csv,
    parse_yahoo_dxy_json,
)


def test_forex_factory_keeps_only_high_impact_usd() -> None:
    text = json.dumps(
        [
            {
                "title": "Non-Farm Employment Change",
                "country": "USD",
                "date": "2026-09-04T08:30:00-04:00",
                "impact": "High",
                "forecast": "75K",
                "previous": "73K",
            },
            {
                "title": "USD medium event",
                "country": "USD",
                "date": "2026-09-04T10:00:00-04:00",
                "impact": "Medium",
            },
            {
                "title": "EUR high event",
                "country": "EUR",
                "date": "2026-09-04T09:00:00+02:00",
                "impact": "High",
            },
        ]
    )
    events = parse_forex_factory_json(text)
    assert len(events) == 1
    assert events[0].name == "Non-Farm Employment Change"
    assert events[0].time_utc == datetime(2026, 9, 4, 12, 30, tzinfo=UTC)
    assert events[0].forecast == "75K"


def test_cftc_parser_extracts_main_gold_managed_money() -> None:
    row = ["0"] * 191
    row[0] = "GOLD - COMMODITY EXCHANGE INC."
    row[2] = "2026-09-01"
    row[13] = "140000"
    row[14] = "10000"
    row[61] = "5000"
    row[62] = "-2000"
    buffer = io.StringIO()
    csv.writer(buffer).writerow(row)

    value = parse_cftc_gold_disaggregated(buffer.getvalue())
    assert value.report_date == "2026-09-01"
    assert value.managed_money_long == 140000
    assert value.managed_money_short == 10000
    assert value.managed_money_net == 130000
    assert value.managed_money_net_change == 7000


def test_yahoo_dxy_parser_calculates_changes_and_trend() -> None:
    timestamps = [
        int(datetime(2026, 8, 25 + offset, tzinfo=UTC).timestamp())
        for offset in range(6)
    ]
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {"close": [98.0, 98.2, 98.5, 99.0, 99.2, 100.0]}
                        ]
                    },
                }
            ],
            "error": None,
        }
    }
    value = parse_yahoo_dxy_json(json.dumps(payload))
    assert value.value == pytest.approx(100.0)
    assert value.change_1d_pct == pytest.approx((100.0 / 99.2 - 1.0) * 100.0)
    assert value.change_5d_pct == pytest.approx((100.0 / 98.0 - 1.0) * 100.0)
    assert value.trend_5d == "UP"
    assert not value.is_proxy


def test_fred_dollar_fallback_is_explicitly_marked_proxy() -> None:
    text = (
        "observation_date,DTWEXBGS\n"
        "2026-08-24,119.0\n"
        "2026-08-25,119.1\n"
        "2026-08-26,119.2\n"
        "2026-08-27,119.3\n"
        "2026-08-28,119.4\n"
        "2026-09-01,119.5\n"
    )
    value = parse_fred_usd_proxy_csv(text)
    assert value.value == pytest.approx(119.5)
    assert value.is_proxy
    assert "proxy" in value.source.casefold()
