"""Load the dedicated FAST_SCALP_M1 snapshot exported by MT5."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from meta_trader_ai.fast_scalp import FastScalpSnapshot


class FastScalpSnapshotError(RuntimeError):
    """Raised when the M1 scalp snapshot is missing, invalid, or stale."""


def load_fast_scalp_snapshot(path: Path, max_age_seconds: int) -> FastScalpSnapshot:
    """Load a fresh M1 snapshot and fail closed on bad or stale market data."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        snapshot = FastScalpSnapshot.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise FastScalpSnapshotError(
            f"Cannot read a valid FAST_SCALP_M1 snapshot: {exc}"
        ) from exc

    generated_at = snapshot.generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - generated_at).total_seconds()
    if age > max_age_seconds:
        raise FastScalpSnapshotError(
            f"FAST_SCALP_M1 snapshot is stale ({age:.1f}s old)"
        )
    return snapshot
