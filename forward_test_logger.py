"""Poll the local read-only signal API and build an immutable demo journal.

This program never connects to a broker and contains no order functions.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


FIELDS = (
    "observed_at_utc",
    "signal_generated_at",
    "symbol",
    "action",
    "confidence",
    "technical_score",
    "news_risk",
    "max_risk_percent",
    "reasons",
)


def fetch_json(url: str, timeout: float = 10.0) -> dict[str, object]:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - localhost by design
        return json.load(response)


def journal_row(payload: dict[str, object]) -> dict[str, object]:
    return {
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "signal_generated_at": payload.get("generated_at", ""),
        "symbol": payload.get("symbol", ""),
        "action": payload.get("action", ""),
        "confidence": payload.get("confidence", ""),
        "technical_score": payload.get("technical_score", ""),
        "news_risk": payload.get("news_risk", ""),
        "max_risk_percent": payload.get("max_risk_percent", ""),
        "reasons": " | ".join(str(x) for x in payload.get("reasons", [])),
    }


def existing_keys(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            (row["signal_generated_at"], row["symbol"], row["action"])
            for row in csv.DictReader(handle)
        }


def append_once(
    path: Path,
    row: dict[str, object],
    seen: set[tuple[str, str, str]],
) -> bool:
    key = (str(row["signal_generated_at"]), str(row["symbol"]), str(row["action"]))
    if not key[0] or key in seen:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()
    seen.add(key)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/hint")
    parser.add_argument("--output", default="forward_journal.csv")
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    output = Path(args.output)
    seen = existing_keys(output)
    while True:
        try:
            payload = fetch_json(args.url)
            row = journal_row(payload)
            if append_once(output, row, seen):
                print(
                    f"saved {row['symbol']} {row['action']} "
                    f"{row['signal_generated_at']}"
                )
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"API unavailable: {exc}")
        if args.once:
            break
        time.sleep(max(args.interval, 5.0))


if __name__ == "__main__":
    main()
