"""Backtest-only diagnostic for the M15 technical score components.

This module does not change the live signal engine. It focuses on the strongest
candidate found so far: BUY signals that are trend-aligned and occur in the
LOW_VOLATILITY regime. For each historical signal it reconstructs the exact
EMA/RSI/momentum components used by the current signal engine, then reports
performance by component bands.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from meta_trader_ai.backtest import (
    BacktestMetrics,
    Candle,
    HistoricalSignal,
    calculate_metrics,
    generate_signals,
    load_candles,
    simulate_trades,
)
from meta_trader_ai.models import Action, MarketSnapshot
from meta_trader_ai.regime import VolatilityRegime
from meta_trader_ai.regime_backtest import is_trend_aligned, label_signals
from meta_trader_ai.signals import _atr, _clamp, _ema, _rsi
from meta_trader_ai.walk_forward import _metrics_for_period, _period_bounds


@dataclass(frozen=True, slots=True)
class ComponentSignal:
    signal: HistoricalSignal
    trend_component: float
    rsi_component: float
    momentum_component: float
    rsi14: float
    ema_gap_atr: float
    momentum_4_atr: float
    spread_to_atr: float


@dataclass(frozen=True, slots=True)
class ComponentRow:
    component: str
    band: str
    min_value: float
    max_value: float | None
    metrics: BacktestMetrics
    year_2024_net_r: float
    year_2025_net_r: float
    year_2026_net_r: float


def _snapshot_for_window(
    window: list[Candle],
    *,
    symbol: str,
    point_size: float,
) -> MarketSnapshot:
    current = window[-1]
    spread_price = current.spread_points * point_size
    return MarketSnapshot(
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


def _candidate_components(
    candles: list[Candle],
    *,
    symbol: str,
    point_size: float,
    lookback_bars: int,
) -> list[ComponentSignal]:
    raw = generate_signals(
        candles,
        symbol=symbol,
        point_size=point_size,
        lookback_bars=lookback_bars,
    )
    labelled = label_signals(candles, raw, lookback_bars=lookback_bars)
    candidates = [
        item
        for item in labelled
        if item.signal.action is Action.BUY
        and is_trend_aligned(item)
        and item.regime.volatility is VolatilityRegime.LOW_VOLATILITY
    ]

    result: list[ComponentSignal] = []
    for item in candidates:
        index = item.signal.candle_index
        start = index - lookback_bars + 1
        window = candles[start : index + 1]
        snapshot = _snapshot_for_window(window, symbol=symbol, point_size=point_size)
        closes = snapshot.closes
        atr14 = _atr(snapshot, 14)
        ema9 = _ema(closes, 9)
        ema21 = _ema(closes, 21)
        rsi14 = _rsi(closes, 14)
        ema_gap_atr = (ema9 - ema21) / atr14
        momentum_4_atr = (closes[-1] - closes[-5]) / atr14
        trend_component = _clamp(ema_gap_atr * 18.0, -45.0, 45.0)
        rsi_component = _clamp((rsi14 - 50.0) * 0.4, -20.0, 20.0)
        momentum_component = _clamp(momentum_4_atr * 4.0, -15.0, 15.0)
        spread_to_atr = (snapshot.ask - snapshot.bid) / atr14
        result.append(
            ComponentSignal(
                signal=item.signal,
                trend_component=trend_component,
                rsi_component=rsi_component,
                momentum_component=momentum_component,
                rsi14=rsi14,
                ema_gap_atr=ema_gap_atr,
                momentum_4_atr=momentum_4_atr,
                spread_to_atr=spread_to_atr,
            )
        )
    return result


def _metrics(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> BacktestMetrics:
    trades, candidates = simulate_trades(
        candles,
        signals,
        min_confidence=70,
        point_size=point_size,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
    )
    return calculate_metrics(trades, threshold=70, candidate_signals=candidates)


def _year_net(
    candles: list[Candle],
    signals: list[HistoricalSignal],
    *,
    year: int,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> float:
    periods = _period_bounds(signals, "year")
    match = next((item for item in periods if item[0] == str(year)), None)
    if match is None:
        return 0.0
    _, start, end = match
    metrics = _metrics_for_period(
        candles,
        signals,
        start=start,
        end=end,
        threshold=70,
        point_size=point_size,
        stop_loss_points=stop_loss_points,
        take_profit_points=take_profit_points,
    )
    return metrics.net_r


def _evaluate_bands(
    candles: list[Candle],
    items: list[ComponentSignal],
    *,
    component: str,
    bands: list[tuple[str, float, float | None]],
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> list[ComponentRow]:
    rows: list[ComponentRow] = []
    for label, low, high in bands:
        selected_items = []
        for item in items:
            value = float(getattr(item, component))
            if value < low:
                continue
            if high is not None and value >= high:
                continue
            selected_items.append(item)
        signals = [item.signal for item in selected_items]
        metrics = _metrics(
            candles,
            signals,
            point_size=point_size,
            stop_loss_points=stop_loss_points,
            take_profit_points=take_profit_points,
        )
        rows.append(
            ComponentRow(
                component=component,
                band=label,
                min_value=low,
                max_value=high,
                metrics=metrics,
                year_2024_net_r=_year_net(
                    candles,
                    signals,
                    year=2024,
                    point_size=point_size,
                    stop_loss_points=stop_loss_points,
                    take_profit_points=take_profit_points,
                ),
                year_2025_net_r=_year_net(
                    candles,
                    signals,
                    year=2025,
                    point_size=point_size,
                    stop_loss_points=stop_loss_points,
                    take_profit_points=take_profit_points,
                ),
                year_2026_net_r=_year_net(
                    candles,
                    signals,
                    year=2026,
                    point_size=point_size,
                    stop_loss_points=stop_loss_points,
                    take_profit_points=take_profit_points,
                ),
            )
        )
    return rows


def evaluate(
    candles: list[Candle],
    items: list[ComponentSignal],
    *,
    point_size: float,
    stop_loss_points: float,
    take_profit_points: float,
) -> list[ComponentRow]:
    specs = {
        "trend_component": [
            ("<10", -100.0, 10.0),
            ("10-20", 10.0, 20.0),
            ("20-30", 20.0, 30.0),
            ("30-40", 30.0, 40.0),
            ("40+", 40.0, None),
        ],
        "rsi14": [
            ("<50", 0.0, 50.0),
            ("50-55", 50.0, 55.0),
            ("55-60", 55.0, 60.0),
            ("60-65", 60.0, 65.0),
            ("65-70", 65.0, 70.0),
            ("70+", 70.0, None),
        ],
        "momentum_4_atr": [
            ("<0", -100.0, 0.0),
            ("0-0.5", 0.0, 0.5),
            ("0.5-1", 0.5, 1.0),
            ("1-1.5", 1.0, 1.5),
            ("1.5-2", 1.5, 2.0),
            ("2+", 2.0, None),
        ],
        "spread_to_atr": [
            ("<0.03", 0.0, 0.03),
            ("0.03-0.06", 0.03, 0.06),
            ("0.06-0.10", 0.06, 0.10),
            ("0.10-0.15", 0.10, 0.15),
            ("0.15+", 0.15, None),
        ],
    }
    rows: list[ComponentRow] = []
    for component, bands in specs.items():
        rows.extend(
            _evaluate_bands(
                candles,
                items,
                component=component,
                bands=bands,
                point_size=point_size,
                stop_loss_points=stop_loss_points,
                take_profit_points=take_profit_points,
            )
        )
    return rows


def print_report(rows: list[ComponentRow]) -> None:
    print("\nTECHNICAL COMPONENT DIAGNOSTIC")
    print("=" * 108)
    print("Strategy: TREND_AND_LOW_BUY_ONLY | Live engine: UNCHANGED")
    print(
        f"{'Component':<18} {'Band':<12} {'Signals':>8} {'Trades':>7} "
        f"{'Win%':>8} {'PF':>7} {'NetR':>8} {'DD':>7} "
        f"{'2024':>7} {'2025':>7} {'2026':>7}"
    )
    print("-" * 108)
    current = None
    for row in rows:
        if current is not None and row.component != current:
            print("-" * 108)
        current = row.component
        m = row.metrics
        print(
            f"{row.component:<18} {row.band:<12} {m.candidate_signals:>8} "
            f"{m.trades:>7} {m.win_rate:>7.2f}% {m.profit_factor:>7.2f} "
            f"{m.net_r:>8.2f} {m.max_drawdown_r:>7.2f} "
            f"{row.year_2024_net_r:>7.2f} {row.year_2025_net_r:>7.2f} "
            f"{row.year_2026_net_r:>7.2f}"
        )


def write_csv(path: Path, rows: list[ComponentRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "component",
                "band",
                "min_value",
                "max_value",
                "candidate_signals",
                "trades",
                "win_rate",
                "profit_factor",
                "net_r",
                "max_drawdown_r",
                "year_2024_net_r",
                "year_2025_net_r",
                "year_2026_net_r",
            ]
        )
        for row in rows:
            m = row.metrics
            writer.writerow(
                [
                    row.component,
                    row.band,
                    row.min_value,
                    "" if row.max_value is None else row.max_value,
                    m.candidate_signals,
                    m.trades,
                    f"{m.win_rate:.6f}",
                    f"{m.profit_factor:.6f}",
                    f"{m.net_r:.6f}",
                    f"{m.max_drawdown_r:.6f}",
                    f"{row.year_2024_net_r:.6f}",
                    f"{row.year_2025_net_r:.6f}",
                    f"{row.year_2026_net_r:.6f}",
                ]
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose EMA/RSI/momentum/spread contributions in historical M15 signals."
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--sl-points", type=float, default=300.0)
    parser.add_argument("--tp-points", type=float, default=600.0)
    parser.add_argument("--lookback-bars", type=int, default=100)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/component_diagnostic.csv"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    candles = load_candles(args.csv_path)
    items = _candidate_components(
        candles,
        symbol=args.symbol,
        point_size=args.point_size,
        lookback_bars=args.lookback_bars,
    )
    rows = evaluate(
        candles,
        items,
        point_size=args.point_size,
        stop_loss_points=args.sl_points,
        take_profit_points=args.tp_points,
    )
    print(
        f"Candles: {len(candles):,} | Candidate regime BUY signals: {len(items):,}"
    )
    print_report(rows)
    write_csv(args.output, rows)
    print(f"\nComponent CSV: {args.output}")


if __name__ == "__main__":
    main()
