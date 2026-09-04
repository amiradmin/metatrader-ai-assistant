"""Import a browser-downloaded Forex Factory JSON file into the persistent cache."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from meta_trader_ai.calendar_service import save_calendar_disk_cache
from meta_trader_ai.economic_calendar import EconomicCalendarError, parse_calendar_payload


def import_calendar_file(source: Path, destination: Path) -> int:
    """Parse a Forex Factory weekly JSON file and persist it as last-known-good cache."""
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EconomicCalendarError(f"cannot read calendar file {source}: {exc}") from exc

    events = parse_calendar_payload(payload)
    save_calendar_disk_cache(destination, events)
    return len(events)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import a browser-downloaded Forex Factory weekly JSON file into "
            "the persistent economic-calendar cache."
        )
    )
    parser.add_argument("source", type=Path, help="Downloaded ff_calendar_thisweek.json")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("data/economic_calendar_cache.json"),
        help="Persistent cache path (default: data/economic_calendar_cache.json)",
    )
    args = parser.parse_args()

    count = import_calendar_file(args.source, args.destination)
    print(f"Imported {count} calendar events -> {args.destination}")


if __name__ == "__main__":
    main()
