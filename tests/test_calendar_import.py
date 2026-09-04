import json
from pathlib import Path

from meta_trader_ai.calendar_import import import_calendar_file
from meta_trader_ai.calendar_service import load_calendar_disk_cache


def test_import_calendar_file(tmp_path: Path) -> None:
    source = tmp_path / "ff_calendar_thisweek.json"
    destination = tmp_path / "economic_calendar_cache.json"
    source.write_text(
        json.dumps(
            [
                {
                    "title": "Non-Farm Employment Change",
                    "country": "USD",
                    "date": "2026-09-04T08:30:00-04:00",
                    "impact": "High",
                    "forecast": "75K",
                    "previous": "73K",
                }
            ]
        ),
        encoding="utf-8",
    )

    count = import_calendar_file(source, destination)

    assert count == 1
    events = load_calendar_disk_cache(
        destination,
        max_age_minutes=1440,
    )
    assert len(events) == 1
    assert events[0].currency == "USD"
    assert events[0].impact == "HIGH"
