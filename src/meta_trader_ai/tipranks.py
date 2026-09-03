"""Optional TipRanks context adapter for the local read-only signal engine."""

import json
from datetime import datetime, timezone
from pathlib import Path

from meta_trader_ai.models import TipRanksContext


class TipRanksContextError(RuntimeError):
    """Raised when a configured TipRanks context file is invalid."""


def normalize_symbol(symbol: str) -> str:
    """Normalize common broker suffixes to a compact market symbol."""
    return "".join(character for character in symbol.upper() if character.isalpha())[:6]


def save_context(path: Path, context: TipRanksContext) -> None:
    """Persist one TipRanks context payload for the local API."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        context.model_dump_json(indent=2),
        encoding="utf-8",
    )


def load_context(
    path: Path,
    symbol: str,
    max_age_minutes: int,
    now: datetime | None = None,
) -> TipRanksContext | None:
    """Load fresh context for the requested symbol; missing/stale data is optional."""
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        context = TipRanksContext.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise TipRanksContextError(f"Cannot read TipRanks context: {exc}") from exc

    if normalize_symbol(context.symbol) != normalize_symbol(symbol):
        return None

    updated_at = context.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    age_minutes = (current - updated_at).total_seconds() / 60.0
    if age_minutes < -5 or age_minutes > max_age_minutes:
        return None

    return context
