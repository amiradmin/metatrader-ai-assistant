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
    "news_coverage",
    "failed_news_sources",
    "risk_guard_status",
    "day_drawdown_percent",
    "spread_to_atr",
    "mtf_status",
    "h1_trend",
    "h1_structure",
    "h1_structure_event",
    "h4_trend",
    "h4_structure",
    "h4_structure_event",
    "tipranks_status",
    "tipranks_adjustment",
    "max_risk_percent",
    "reasons",
)


def fetch_json(url: str, timeout: float = 30.0) -> dict[str, object]:
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
        "news_coverage": payload.get("news_coverage", ""),
        "failed_news_sources": payload.get("failed_news_sources", ""),
        "risk_guard_status": payload.get("risk_guard_status", ""),
        "day_drawdown_percent": payload.get("day_drawdown_percent", ""),
        "spread_to_atr": payload.get("spread_to_atr", ""),
        "mtf_status": payload.get("mtf_status", ""),
        "h1_trend": payload.get("h1_trend", ""),
        "h1_structure": payload.get("h1_structure", ""),
        "h1_structure_event": payload.get("h1_structure_event", ""),
        "h4_trend": payload.get("h4_trend", ""),
        "h4_structure": payload.get("h4_structure", ""),
        "h4_structure_event": payload.get("h4_structure_event", ""),
        "tipranks_status": payload.get("tipranks_status", ""),
        "tipranks_adjustment": payload.get("tipranks_adjustment", ""),
        "max_risk_percent": payload.get("max_risk_percent", ""),
        "reasons": " | ".join(str(x) for x in payload.get("reasons", [])),
    }


def signal_bucket(value: object) -> str:
    """Return the UTC M15 candle bucket for an ISO-8601 signal timestamp."""
    raw = str(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return raw
    bucket = parsed.replace(
        minute=(parsed.minute // 15) * 15,
        second=0,
        microsecond=0,
    )
    return bucket.isoformat()


def ensure_schema(path: Path) -> None:
    """Add newly introduced observer columns without discarding old rows."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        existing = tuple(reader.fieldnames or ())
        if existing == FIELDS:
            return
        rows = list(reader)

    temporary = path.with_suffix(path.suffix + ".schema_tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})
    temporary.replace(path)


def existing_keys(path: Path) -> set[tuple[str, str]]:
    ensure_schema(path)
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            (signal_bucket(row["signal_generated_at"]), row["symbol"])
            for row in csv.DictReader(handle)
        }


def append_once(
    path: Path,
    row: dict[str, object],
    seen: set[tuple[str, str]],
) -> bool:
    key = (signal_bucket(row["signal_generated_at"]), str(row["symbol"]))
    if not key[0] or key in seen:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_schema(path)
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
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    output = Path(args.output)
    seen = existing_keys(output)
    while True:
        try:
            payload = fetch_json(args.url, timeout=max(args.timeout, 1.0))
            row = journal_row(payload)
            if append_once(output, row, seen):
                print(
                    f"saved {row['symbol']} {row['action']} "
                    f"conf={row['confidence']} guard={row['risk_guard_status']} "
                    f"{row['signal_generated_at']}"
                )
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"API unavailable: {exc}")
        if args.once:
            break
        time.sleep(max(args.interval, 5.0))


if __name__ == "__main__":
    main()
