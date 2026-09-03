"""Historical M15 backtester for the current explainable signal engine.

The backtester deliberately uses only information available at signal time.
Signals are calculated from completed candles and filled at the next candle open.
Historical news and TipRanks context are not reconstructed in this baseline.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meta_trader_ai.models import Action, MarketSnapshot
from meta_trader_ai.signals import MIN_ACTION_CONFIDENCE, build_hint


@dataclass(frozen=True, slots=True)
class Candle:
    """One chronological MT5 OHLC candle exported by HistoricalCsvExporter."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread_points: float
    real_volume: int


@dataclass(frozen=True, slots=True)
class HistoricalSignal:
    """Directional API-style signal generated after one completed M15 candle."""

    candle_index: int
    time: datetime
    action: Action
    confidence: int
    technical_score: int


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    """One simulated trade using the current demo execution assumptions."""

    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    action: Action
    confidence: int
    technical_score: int
    entry_price: float
    exit_price: float
    outcome: str
    pnl_price: float
    pnl_r: float
    holding_bars: int


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    """Summary statistics for one minimum-confidence threshold."""

    threshold: int
    candidate_signals: int
    trades: int
    buy_trades: int
    sell_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float
    profit_factor: float
    net_r: float
    max_drawdown_r: float
    average_win_r: float
    average_loss_r: float
    average_holding_bars: float


def load_candles(path: Path) -> list[Candle]:
    """Load chronological candles from the MT5 CSV export."""
    candles: list[Candle] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "time",
            "open",
            "high",
            "low",
            "close",
            "tick_volume",
            "spread",
            "real_volume",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"CSV must contain columns: {', '.join(sorted(required))}")

        for row in reader:
            candles.append(
                Candle(
                    time=datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S"),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    tick_volume=int(row["tick_volume"]),
                    spread_points=float(row["spread"]),
                    real_volume=int(row["real_volume"]),
                )
            )

    if len(candles) < 101:
        raise ValueError("At least 101 M15 candles are required for backtesting")
    if any(current.time <= previous.time for previous, current in zip(candles, candles[1:])):
        raise ValueError("CSV candles must be strictly chronological")
    return candles


def generate_signals(
    candles: list[Candle],
    *,
    symbol: str,
    point_size: float,
    lookback_bars: int = 100,
) -> list[HistoricalSignal]:
    """Run the live M15 signal engine over completed historical windows."""
    if point_size <= 0:
        raise ValueError("point_size must be positive")
    if lookback_bars < 21:
        raise ValueError("lookback_bars must be at least 21")

    signals: list[HistoricalSignal] = []
    # The final candle cannot create a trade because there is no next-bar open.
    for index in range(lookback_bars - 1, len(candles) - 1):
        window = candles[index - lookback_bars + 1 : index + 1]
        current = candles[index]
        spread_price = current.spread_points * point_size
        snapshot = MarketSnapshot(
            symbol=symbol,
            timeframe="M15",
            generated_at=current.time,
            bid=current.close,
            ask=current.close + spread_price,
            balance=10_000.0,
            equity=10_000.0,
            positions_total=0,
            opens=[candle.open for candle in window],
            highs=[candle.high for candle in window],
            lows=[candle.low for candle in window],
            closes=[candle.close for candle in window],
        )
        hint = build_hint(
            snapshot,
            news=[],
            max_risk_percent=0.5,
            tipranks_context=None,
        )
        if hint.action in {Action.BUY, Action.SELL}:
            signals.append(
                HistoricalSignal(
                    candle_index=index,
                    time=current.time,
                    action=hint.action,
                    confidence=hint.confidence,
                    technical_score=hint.technical_score,
                )
            )
    return signals


def _resolve_trade(
    candles: list[Candle],
    signal: HistoricalSignal,
    *,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> tuple[BacktestTrade, int]:
    """Fill at next open and resolve SL/TP with a conservative same-bar rule."""
    entry_index = signal.candle_index + 1
    entry_candle = candles[entry_index]
    entry_spread = entry_candle.spread_points * point_size
    stop_distance = stop_loss_points * point_size
    target_distance = take_profit_points * point_size

    if signal.action is Action.BUY:
        entry_price = entry_candle.open + entry_spread
        stop_price = entry_price - stop_distance
        target_price = entry_price + target_distance
    else:
        entry_price = entry_candle.open
        stop_price = entry_price + stop_distance
        target_price = entry_price - target_distance

    exit_index = len(candles) - 1
    outcome = "END_OF_DATA"
    exit_price: float | None = None

    for index in range(entry_index, len(candles)):
        candle = candles[index]
        spread = candle.spread_points * point_size

        if signal.action is Action.BUY:
            stop_hit = candle.low <= stop_price
            target_hit = candle.high >= target_price
        else:
            # A short position is closed at Ask, so shift the historical Bid bar
            # by the recorded spread before evaluating its protective orders.
            ask_high = candle.high + spread
            ask_low = candle.low + spread
            stop_hit = ask_high >= stop_price
            target_hit = ask_low <= target_price

        # OHLC does not reveal the intrabar path. If both levels were touched in
        # one candle, assume the stop was hit first rather than overstate results.
        if stop_hit:
            exit_index = index
            exit_price = stop_price
            outcome = "STOP"
            break
        if target_hit:
            exit_index = index
            exit_price = target_price
            outcome = "TARGET"
            break

    if exit_price is None:
        final = candles[exit_index]
        if signal.action is Action.BUY:
            exit_price = final.close
        else:
            exit_price = final.close + final.spread_points * point_size

    pnl_price = (
        exit_price - entry_price
        if signal.action is Action.BUY
        else entry_price - exit_price
    )
    pnl_r = pnl_price / stop_distance

    return (
        BacktestTrade(
            signal_time=signal.time,
            entry_time=entry_candle.time,
            exit_time=candles[exit_index].time,
            action=signal.action,
            confidence=signal.confidence,
            technical_score=signal.technical_score,
            entry_price=entry_price,
            exit_price=exit_price,
            outcome=outcome,
            pnl_price=pnl_price,
            pnl_r=pnl_r,
            holding_bars=exit_index - entry_index + 1,
        ),
        exit_index,
    )


def simulate_trades(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    min_confidence: int,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> tuple[list[BacktestTrade], int]:
    """Simulate a maximum of one open position, matching the first demo test."""
    if min_confidence < MIN_ACTION_CONFIDENCE:
        raise ValueError(
            f"min_confidence cannot be below the signal engine floor "
            f"({MIN_ACTION_CONFIDENCE})"
        )
    if stop_loss_points <= 0 or take_profit_points <= 0:
        raise ValueError("SL and TP points must be positive")

    eligible = [signal for signal in signals if signal.confidence >= min_confidence]
    trades: list[BacktestTrade] = []
    blocked_until_index = -1

    for signal in eligible:
        entry_index = signal.candle_index + 1
        if entry_index <= blocked_until_index:
            continue
        trade, exit_index = _resolve_trade(
            candles,
            signal,
            point_size=point_size,
            stop_loss_points=stop_loss_points,
            take_profit_points=take_profit_points,
        )
        trades.append(trade)
        blocked_until_index = exit_index

    return trades, len(eligible)


def calculate_metrics(
    trades: list[BacktestTrade],
    *,
    threshold: int,
    candidate_signals: int,
) -> BacktestMetrics:
    """Calculate expectancy-style metrics in risk (R) units."""
    wins = [trade for trade in trades if trade.pnl_r > 1e-12]
    losses = [trade for trade in trades if trade.pnl_r < -1e-12]
    breakeven = len(trades) - len(wins) - len(losses)

    gross_profit = sum(trade.pnl_r for trade in wins)
    gross_loss = abs(sum(trade.pnl_r for trade in losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = math.inf
    else:
        profit_factor = 0.0

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        cumulative += trade.pnl_r
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    return BacktestMetrics(
        threshold=threshold,
        candidate_signals=candidate_signals,
        trades=len(trades),
        buy_trades=sum(trade.action is Action.BUY for trade in trades),
        sell_trades=sum(trade.action is Action.SELL for trade in trades),
        wins=len(wins),
        losses=len(losses),
        breakeven=breakeven,
        win_rate=(len(wins) / len(trades) * 100.0) if trades else 0.0,
        profit_factor=profit_factor,
        net_r=sum(trade.pnl_r for trade in trades),
        max_drawdown_r=max_drawdown,
        average_win_r=(gross_profit / len(wins)) if wins else 0.0,
        average_loss_r=(sum(trade.pnl_r for trade in losses) / len(losses)) if losses else 0.0,
        average_holding_bars=(
            sum(trade.holding_bars for trade in trades) / len(trades) if trades else 0.0
        ),
    )


def run_backtest(
    candles: list[Candle],
    *,
    symbol: str,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
    thresholds: list[int],
    lookback_bars: int = 100,
) -> tuple[list[BacktestMetrics], dict[int, list[BacktestTrade]]]:
    """Generate signals once, then evaluate several confidence thresholds."""
    signals = generate_signals(
        candles,
        symbol=symbol,
        point_size=point_size,
        lookback_bars=lookback_bars,
    )
    metrics: list[BacktestMetrics] = []
    trades_by_threshold: dict[int, list[BacktestTrade]] = {}
    for threshold in thresholds:
        trades, candidates = simulate_trades(
            candles,
            signals,
            min_confidence=threshold,
            point_size=point_size,
            stop_loss_points=stop_loss_points,
            take_profit_points=take_profit_points,
        )
        trades_by_threshold[threshold] = trades
        metrics.append(
            calculate_metrics(
                trades,
                threshold=threshold,
                candidate_signals=candidates,
            )
        )
    return metrics, trades_by_threshold


def write_trades(path: Path, trades: list[BacktestTrade]) -> None:
    """Persist a trade journal CSV for later diagnosis and walk-forward work."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "signal_time",
                "entry_time",
                "exit_time",
                "action",
                "confidence",
                "technical_score",
                "entry_price",
                "exit_price",
                "outcome",
                "pnl_price",
                "pnl_r",
                "holding_bars",
            ]
        )
        for trade in trades:
            writer.writerow(
                [
                    trade.signal_time.isoformat(sep=" "),
                    trade.entry_time.isoformat(sep=" "),
                    trade.exit_time.isoformat(sep=" "),
                    trade.action.value,
                    trade.confidence,
                    trade.technical_score,
                    f"{trade.entry_price:.5f}",
                    f"{trade.exit_price:.5f}",
                    trade.outcome,
                    f"{trade.pnl_price:.5f}",
                    f"{trade.pnl_r:.5f}",
                    trade.holding_bars,
                ]
            )


def _format_number(value: float) -> str:
    if math.isinf(value):
        return "inf"
    return f"{value:.2f}"


def print_report(
    candles: list[Candle],
    metrics: list[BacktestMetrics],
    *,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> None:
    """Print a compact terminal report suitable for pasting into the chat."""
    print("\nM15 BASELINE BACKTEST")
    print("=" * 88)
    print(
        f"Candles: {len(candles):,} | "
        f"Range: {candles[0].time} -> {candles[-1].time}"
    )
    print(
        f"Point size: {point_size:g} | SL: {stop_loss_points:g} points | "
        f"TP: {take_profit_points:g} points | Max open trades: 1"
    )
    print("Historical news/TipRanks: excluded (technical + recorded spread baseline)")
    print("Entry: next-bar open | Same-bar SL+TP: STOP first (conservative)")
    print()
    print(
        f"{'Conf':>5} {'Signals':>8} {'Trades':>7} {'Buy':>6} {'Sell':>6} "
        f"{'Win%':>7} {'PF':>7} {'Net R':>9} {'MaxDD R':>9} {'AvgBars':>8}"
    )
    print("-" * 88)
    for item in metrics:
        print(
            f"{item.threshold:>5} {item.candidate_signals:>8} {item.trades:>7} "
            f"{item.buy_trades:>6} {item.sell_trades:>6} "
            f"{item.win_rate:>6.2f}% {_format_number(item.profit_factor):>7} "
            f"{item.net_r:>9.2f} {item.max_drawdown_r:>9.2f} "
            f"{item.average_holding_bars:>8.2f}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest the current M15 XAUUSD signal engine without look-ahead."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        type=Path,
        default=Path("data/xauusd_m15_history.csv"),
    )
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--sl-points", type=float, default=300.0)
    parser.add_argument("--tp-points", type=float, default=600.0)
    parser.add_argument("--lookback-bars", type=int, default=100)
    parser.add_argument(
        "--thresholds",
        default="70,75,80,85,90",
        help="Comma-separated confidence thresholds, all >= 70.",
    )
    parser.add_argument(
        "--journal-threshold",
        type=int,
        default=75,
        help="Threshold whose executed trades are written to --journal-output.",
    )
    parser.add_argument(
        "--journal-output",
        type=Path,
        default=Path("data/backtest_trades_conf75.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    thresholds = sorted({int(value.strip()) for value in args.thresholds.split(",")})
    if not thresholds or any(value < MIN_ACTION_CONFIDENCE for value in thresholds):
        raise SystemExit(
            f"All thresholds must be >= {MIN_ACTION_CONFIDENCE}: {thresholds}"
        )

    candles = load_candles(args.csv_path)
    metrics, trades_by_threshold = run_backtest(
        candles,
        symbol=args.symbol,
        point_size=args.point_size,
        stop_loss_points=args.sl_points,
        take_profit_points=args.tp_points,
        thresholds=thresholds,
        lookback_bars=args.lookback_bars,
    )
    print_report(
        candles,
        metrics,
        point_size=args.point_size,
        stop_loss_points=args.sl_points,
        take_profit_points=args.tp_points,
    )

    if args.journal_threshold not in trades_by_threshold:
        raise SystemExit(
            "--journal-threshold must also be included in --thresholds "
            f"({args.journal_threshold} not in {thresholds})"
        )
    write_trades(args.journal_output, trades_by_threshold[args.journal_threshold])
    print(
        f"\nTrade journal ({args.journal_threshold}+ confidence): "
        f"{args.journal_output}"
    )


if __name__ == "__main__":
    main()
