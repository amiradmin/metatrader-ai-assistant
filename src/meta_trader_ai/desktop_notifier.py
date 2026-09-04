"""Show Ubuntu desktop alerts for guarded, directional MT5 hints.

This module polls the local read-only ``/hint`` API. It never connects to a
broker and it never submits, modifies, or closes an order.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from meta_trader_ai.forward_logger import signal_bucket
from meta_trader_ai.models import Action

__all__ = ["DesktopNotification", "is_notifiable", "main", "notification_from_payload"]


@dataclass(frozen=True, kw_only=True)
class DesktopNotification:
    """A local, human-review-only desktop alert."""

    title: str
    body: str
    key: tuple[str, str]


def fetch_json(url: str, timeout: float = 30.0) -> dict[str, object]:
    """Fetch one local hint response."""
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - localhost by design
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("The local hint API returned a non-object JSON payload")
    return payload


def is_notifiable(
    payload: Mapping[str, object],
    *,
    minimum_confidence: int = 75,
) -> bool:
    """Return whether the API has approved a strict directional hint for review."""
    try:
        confidence = int(payload.get("confidence", 0))
    except (TypeError, ValueError):
        return False
    return (
        str(payload.get("action", "")) in {Action.BUY.value, Action.SELL.value}
        and str(payload.get("risk_guard_status", "")) == "OK"
        and confidence >= minimum_confidence
    )


def notification_from_payload(payload: Mapping[str, object]) -> DesktopNotification:
    """Create an alert that makes the safety context visible to the user."""
    action = str(payload["action"])
    symbol = str(payload.get("symbol", "UNKNOWN"))
    generated_at = str(payload.get("generated_at", ""))
    key = (signal_bucket(generated_at), symbol)
    title = f"MetaTrader AI: {action} review — {symbol}"
    body = " | ".join(
        (
            f"confidence {payload.get('confidence', 'n/a')}/100",
            f"news {payload.get('news_risk', 'n/a')}",
            f"coverage {payload.get('news_coverage', 'n/a')}",
            f"MTF {payload.get('mtf_status', 'n/a')}",
            f"max risk {payload.get('max_risk_percent', 'n/a')}%",
            "manual review required; no order was placed",
        )
    )
    return DesktopNotification(title=title, body=body, key=key)


def send_notification(notification: DesktopNotification) -> None:
    """Send one Ubuntu notification through the installed notify-send utility."""
    executable = shutil.which("notify-send")
    if executable is None:
        raise RuntimeError(
            "notify-send is unavailable. Install libnotify-bin, then start the notifier again."
        )
    subprocess.run(
        [
            executable,
            "--app-name=MetaTrader AI Assistant",
            "--urgency=normal",
            "--expire-time=20000",
            notification.title,
            notification.body,
        ],
        check=True,
    )


def run(
    *,
    url: str,
    interval: float,
    timeout: float,
    once: bool,
    minimum_confidence: int = 75,
    sender: Callable[[DesktopNotification], None] = send_notification,
) -> None:
    """Poll the local API and alert once per symbol and completed M15 candle."""
    alerted: set[tuple[str, str]] = set()
    while True:
        try:
            payload = fetch_json(url, timeout=max(timeout, 1.0))
            if is_notifiable(payload, minimum_confidence=minimum_confidence):
                notification = notification_from_payload(payload)
                if notification.key not in alerted:
                    sender(notification)
                    alerted.add(notification.key)
                    print(f"alerted {notification.title} at {notification.key[0]}")
        except (
            HTTPError,
            URLError,
            TimeoutError,
            ValueError,
            RuntimeError,
            OSError,
            subprocess.CalledProcessError,
        ) as exc:
            print(f"Notifier skipped this poll: {exc}")
        if once:
            return
        time.sleep(max(interval, 5.0))


def main() -> None:
    """Run the local Ubuntu notification watcher."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/hint")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--min-confidence", type=int, default=75)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run(
        url=args.url,
        interval=args.interval,
        timeout=args.timeout,
        once=args.once,
        minimum_confidence=args.min_confidence,
    )


if __name__ == "__main__":
    main()
