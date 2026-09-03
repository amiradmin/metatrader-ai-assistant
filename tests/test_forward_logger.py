import csv
from pathlib import Path

from forward_test_logger import append_once, journal_row, signal_bucket


def payload(generated_at: str, action: str = "WAIT") -> dict[str, object]:
    return {
        "generated_at": generated_at,
        "symbol": "XAUUSD_o",
        "action": action,
        "confidence": 55,
        "technical_score": 60,
        "news_risk": "LOW",
        "max_risk_percent": 0.5,
        "reasons": ["test"],
    }


def test_signal_bucket_uses_utc_m15_boundary() -> None:
    assert signal_bucket("2026-09-03T04:45:57.523604Z") == (
        "2026-09-03T04:45:00+00:00"
    )


def test_only_first_signal_per_symbol_and_m15_candle_is_saved(
    tmp_path: Path,
) -> None:
    journal = tmp_path / "journal.csv"
    seen: set[tuple[str, str]] = set()

    assert append_once(
        journal,
        journal_row(payload("2026-09-03T04:45:01Z", "BUY")),
        seen,
    )
    assert not append_once(
        journal,
        journal_row(payload("2026-09-03T04:59:59Z", "SELL")),
        seen,
    )
    assert append_once(
        journal,
        journal_row(payload("2026-09-03T05:00:00Z", "WAIT")),
        seen,
    )

    with journal.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
