"""Read snapshots exported by the read-only MQL5 bridge."""

import json
from datetime import datetime, timezone
from pathlib import Path

from meta_trader_ai.models import MarketSnapshot


class SnapshotError(RuntimeError):
    """Raised when an MT5 snapshot is missing, invalid, or stale."""


def load_snapshot(path: Path, max_age_seconds: int) -> MarketSnapshot:
    """Load a snapshot and reject stale market data."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        snapshot = MarketSnapshot.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SnapshotError(f"Cannot read a valid MT5 snapshot: {exc}") from exc

    generated_at = snapshot.generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - generated_at).total_seconds()
    if age > max_age_seconds:
        raise SnapshotError(f"MT5 snapshot is stale ({age:.1f}s old)")
    return snapshot
