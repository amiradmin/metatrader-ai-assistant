"""Walk-forward parameter learning for the unified EA historical simulator.

Learning is deliberately offline and conservative. Risk percent, daily-loss
limit and spread controls stay frozen. A small grid of strategy parameters is
ranked on a rolling training window, then the winner is evaluated on the next
unseen test window. The live/demo EA is never modified automatically.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date
from itertools import product
from pathlib import Path

from meta_trader_ai.backtest import Candle, load_candles
from meta_trader_ai.ea_simulator import (
    EAParameters,
    HistoricalDecision,
    SimulatedTrade,
    SimulationMetrics,
    _calculate_metrics,
    generate_decisions,
    simulate_ea,
    write_trade_journal,
)


@dataclass(frozen=True, slots=True)
class LearningFold:
    fold: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    selected: EAParameters
    train_score: float
    train_metrics: SimulationMetrics
    test_metrics: SimulationMetrics


@dataclass(slots=True)
class LearningResult:
    folds: list[LearningFold]
    oos_trades: list[SimulatedTrade]
    oos_daily_pnl: dict[date, float]
    oos_metrics: SimulationMetrics
    latest_candidate: EAParameters
    selected_counts: Counter[str]


def _parameter_key(params: EAParameters) -> str:
    return (
        f"conf={params.min_confidence},atr={params.atr_multiplier:.2f},"
        f"rr={params.reward_risk_ratio:.2f},ext={params.max_extension_atr:.2f}"
    )


def candidate_grid(base: EAParameters, *, wide: bool = False) -> list[EAParameters]:
    """Return a bounded grid that never optimizes risk or daily-loss limits."""
    confidences = sorted({base.min_confidence, 75, 80, 85})
    atr_multipliers = sorted({base.atr_multiplier, 1.25, 1.50})
    reward_risks = sorted({base.reward_risk_ratio, 1.50, 2.00})
    max_extensions = sorted({base.max_extension_atr, 1.00, 1.50, 2.00})
    if wide:
        atr_multipliers = sorted(set(atr_multipliers) | {1.75})
        reward_risks = sorted(set(reward_risks) | {2.50})

    return [
        replace(
            base,
            min_confidence=confidence,
            atr_multiplier=atr_multiplier,
            reward_risk_ratio=reward_risk,
            max_extension_atr=max_extension,
        )
        for confidence, atr_multiplier, reward_risk, max_extension in product(
            confidences,
            atr_multipliers,
            reward_risks,
            max_extensions,
        )
    ]


def _window_indices(
    candles: list[Candle],
    start_day: date,
    end_day: date,
) -> tuple[int, int]:
    first = next(
        index for index, candle in enumerate(candles) if candle.time.date() >= start_day
    )
    last = next(
        index
        for index in range(len(candles) - 1, -1, -1)
        if candles[index].time.date() <= end_day
    )
    return first, last


def training_score(metrics: SimulationMetrics, *, min_trades: int) -> float:
    """Robust-ish selection score: reward repeatable R, penalize drawdown/few trades."""
    if metrics.trades < min_trades:
        return -math.inf
    trade_weight = math.sqrt(min(metrics.trades, 100))
    pf = min(metrics.profit_factor, 3.0) if math.isfinite(metrics.profit_factor) else 3.0
    return (
        metrics.expectancy_r * trade_weight
        + 0.10 * pf
        - 0.12 * metrics.max_drawdown_r
    )


def _best_candidate(
    candles: list[Candle],
    decisions: list[HistoricalDecision],
    candidates: list[EAParameters],
    *,
    point_size: float,
    initial_balance: float,
    daily_goal_usd: float,
    start_index: int,
    end_index: int,
    min_train_trades: int,
) -> tuple[EAParameters, float, SimulationMetrics]:
    best_params = candidates[0]
    best_score = -math.inf
    best_metrics: SimulationMetrics | None = None

    for params in candidates:
        result = simulate_ea(
            candles,
            decisions,
            params=params,
            point_size=point_size,
            initial_balance=initial_balance,
            daily_goal_usd=daily_goal_usd,
            start_index=start_index,
            end_index=end_index,
        )
        score = training_score(result.metrics, min_trades=min_train_trades)
        if score > best_score:
            best_params = params
            best_score = score
            best_metrics = result.metrics

    if best_metrics is None:
        # If every candidate had too few trades, keep the frozen/current settings
        # and report the score as -inf rather than picking a lucky tiny sample.
        fallback = simulate_ea(
            candles,
            decisions,
            params=best_params,
            point_size=point_size,
            initial_balance=initial_balance,
            daily_goal_usd=daily_goal_usd,
            start_index=start_index,
            end_index=end_index,
        )
        best_metrics = fallback.metrics
    return best_params, best_score, best_metrics


def run_learning(
    candles: list[Candle],
    decisions: list[HistoricalDecision],
    *,
    base_params: EAParameters,
    candidates: list[EAParameters],
    point_size: float,
    initial_balance: float = 1_000.0,
    daily_goal_usd: float = 10.0,
    train_days: int = 60,
    test_days: int = 20,
    min_train_trades: int = 10,
) -> LearningResult:
    """Run rolling train->unseen-test folds and carry balance through OOS tests."""
    if train_days < 20 or test_days < 5:
        raise ValueError("train_days must be >=20 and test_days >=5")
    if min_train_trades < 1:
        raise ValueError("min_train_trades must be positive")

    first_entry = min(item.candle_index for item in decisions) + 1
    last_entry = min(len(candles) - 1, max(item.candle_index for item in decisions) + 1)
    trading_dates = sorted(
        {candles[index].time.date() for index in range(first_entry, last_entry + 1)}
    )
    if len(trading_dates) < train_days + test_days:
        raise ValueError(
            f"Need at least {train_days + test_days} trading days after warmup; "
            f"only {len(trading_dates)} are available"
        )

    folds: list[LearningFold] = []
    oos_trades: list[SimulatedTrade] = []
    oos_daily_pnl: defaultdict[date, float] = defaultdict(float)
    oos_dates: list[date] = []
    selected_counts: Counter[str] = Counter()
    carried_balance = initial_balance
    fold_number = 0

    test_start_position = train_days
    while test_start_position + test_days <= len(trading_dates):
        fold_number += 1
        train_window = trading_dates[
            test_start_position - train_days : test_start_position
        ]
        test_window = trading_dates[
            test_start_position : test_start_position + test_days
        ]
        train_start_index, train_end_index = _window_indices(
            candles, train_window[0], train_window[-1]
        )
        test_start_index, test_end_index = _window_indices(
            candles, test_window[0], test_window[-1]
        )

        selected, score, train_metrics = _best_candidate(
            candles,
            decisions,
            candidates,
            point_size=point_size,
            initial_balance=initial_balance,
            daily_goal_usd=daily_goal_usd,
            start_index=train_start_index,
            end_index=train_end_index,
            min_train_trades=min_train_trades,
        )
        selected_counts[_parameter_key(selected)] += 1

        test_result = simulate_ea(
            candles,
            decisions,
            params=selected,
            point_size=point_size,
            initial_balance=carried_balance,
            daily_goal_usd=daily_goal_usd,
            start_index=test_start_index,
            end_index=test_end_index,
        )
        carried_balance = test_result.metrics.end_balance
        oos_trades.extend(test_result.trades)
        oos_dates.extend(test_window)
        for day, pnl in test_result.daily_pnl.items():
            oos_daily_pnl[day] += pnl

        folds.append(
            LearningFold(
                fold=fold_number,
                train_start=train_window[0],
                train_end=train_window[-1],
                test_start=test_window[0],
                test_end=test_window[-1],
                selected=selected,
                train_score=score,
                train_metrics=train_metrics,
                test_metrics=test_result.metrics,
            )
        )
        test_start_position += test_days

    if not folds:
        raise ValueError("No complete walk-forward fold could be constructed")

    unique_oos_dates = sorted(set(oos_dates))
    for day in unique_oos_dates:
        oos_daily_pnl.setdefault(day, 0.0)
    oos_metrics = _calculate_metrics(
        oos_trades,
        start_balance=initial_balance,
        end_balance=carried_balance,
        trading_dates=unique_oos_dates,
        daily_pnl=dict(oos_daily_pnl),
        daily_goal_usd=daily_goal_usd,
    )

    # Candidate for the NEXT unseen period: train only on the latest rolling
    # window, including the most recent data. It is written for review, not auto-applied.
    latest_train_dates = trading_dates[-train_days:]
    latest_start, latest_end = _window_indices(
        candles, latest_train_dates[0], latest_train_dates[-1]
    )
    latest_candidate, _, _ = _best_candidate(
        candles,
        decisions,
        candidates,
        point_size=point_size,
        initial_balance=initial_balance,
        daily_goal_usd=daily_goal_usd,
        start_index=latest_start,
        end_index=latest_end,
        min_train_trades=min_train_trades,
    )

    return LearningResult(
        folds=folds,
        oos_trades=oos_trades,
        oos_daily_pnl=dict(sorted(oos_daily_pnl.items())),
        oos_metrics=oos_metrics,
        latest_candidate=latest_candidate,
        selected_counts=selected_counts,
    )


def write_folds(path: Path, folds: list[LearningFold]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "fold",
                "train_start",
                "train_end",
                "test_start",
                "test_end",
                "confidence",
                "atr_multiplier",
                "reward_risk_ratio",
                "max_extension_atr",
                "train_score",
                "train_trades",
                "train_expectancy_r",
                "train_net_r",
                "train_max_dd_r",
                "test_trades",
                "test_expectancy_r",
                "test_net_r",
                "test_max_dd_r",
                "test_net_usd",
                "test_avg_daily_usd",
                "test_end_balance",
            ]
        )
        for item in folds:
            writer.writerow(
                [
                    item.fold,
                    item.train_start.isoformat(),
                    item.train_end.isoformat(),
                    item.test_start.isoformat(),
                    item.test_end.isoformat(),
                    item.selected.min_confidence,
                    f"{item.selected.atr_multiplier:.2f}",
                    f"{item.selected.reward_risk_ratio:.2f}",
                    f"{item.selected.max_extension_atr:.2f}",
                    f"{item.train_score:.6f}" if math.isfinite(item.train_score) else "-inf",
                    item.train_metrics.trades,
                    f"{item.train_metrics.expectancy_r:.6f}",
                    f"{item.train_metrics.net_r:.6f}",
                    f"{item.train_metrics.max_drawdown_r:.6f}",
                    item.test_metrics.trades,
                    f"{item.test_metrics.expectancy_r:.6f}",
                    f"{item.test_metrics.net_r:.6f}",
                    f"{item.test_metrics.max_drawdown_r:.6f}",
                    f"{item.test_metrics.net_usd:.2f}",
                    f"{item.test_metrics.average_daily_pnl_usd:.2f}",
                    f"{item.test_metrics.end_balance:.2f}",
                ]
            )


def render_learning_report(result: LearningResult, *, train_days: int, test_days: int) -> str:
    metrics = result.oos_metrics
    pf = "inf" if math.isinf(metrics.profit_factor) else f"{metrics.profit_factor:.3f}"
    latest = result.latest_candidate
    lines = [
        "META TRADER AI - WALK-FORWARD LEARNING",
        "=" * 86,
        f"Rolling design: train {train_days} trading days -> test next {test_days} unseen days",
        f"Completed folds: {len(result.folds)}",
        "Risk, daily-loss ceiling and spread guards are FROZEN and never optimized.",
        "The live/demo EA is NOT modified automatically.",
        "",
        "Out-of-sample aggregate",
        f"  Trades: {metrics.trades} | Win rate: {metrics.win_rate:.2f}% | PF: {pf}",
        f"  Expectancy: {metrics.expectancy_r:+.3f} R/trade | Net: {metrics.net_r:+.2f} R",
        f"  Max DD: {metrics.max_drawdown_r:.2f} R | ${metrics.max_drawdown_usd:,.2f}",
        f"  Start/End balance: ${metrics.start_balance:,.2f} -> ${metrics.end_balance:,.2f}",
        f"  OOS average P/L/day: ${metrics.average_daily_pnl_usd:+.2f}",
        f"  $10/day progress: {metrics.daily_goal_progress_percent:.1f}%",
        "",
        "Latest candidate for the NEXT unseen demo period (review only)",
        f"  confidence={latest.min_confidence}",
        f"  ATR multiplier={latest.atr_multiplier:.2f}",
        f"  RR={latest.reward_risk_ratio:.2f}",
        f"  max extension={latest.max_extension_atr:.2f} ATR",
        "",
        "Most frequently selected parameter sets",
    ]
    for key, count in result.selected_counts.most_common(5):
        lines.append(f"  {count:>3} fold(s): {key}")
    lines.extend(
        [
            "",
            "Rule: only repeated positive OUT-OF-SAMPLE evidence can justify a manual demo candidate change.",
            "Historical simulation is evidence, not a guarantee of future profit.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_candidate(path: Path, result: LearningResult, *, train_days: int, test_days: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "REVIEW_ONLY_DO_NOT_AUTO_APPLY",
        "train_days": train_days,
        "test_days": test_days,
        "latest_candidate": asdict(result.latest_candidate),
        "walk_forward_oos": asdict(result.oos_metrics),
        "selected_counts": dict(result.selected_counts),
        "limitations": [
            "Historical news and TipRanks are not reconstructed.",
            "M15 OHLC cannot reveal exact intrabar path; stop-first is used when ambiguous.",
            "Money sizing does not reconstruct broker minimum/lot-step constraints.",
        ],
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--symbol", default="XAUUSD_o")
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--daily-goal", type=float, default=10.0)
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--test-days", type=int, default=20)
    parser.add_argument("--min-train-trades", type=int, default=10)
    parser.add_argument("--wide-grid", action="store_true")
    parser.add_argument("--lookback-bars", type=int, default=100)
    parser.add_argument("--output-prefix", type=Path, default=Path("data/ea_learning"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    candles = load_candles(args.csv_path)
    decisions = generate_decisions(
        candles,
        symbol=args.symbol,
        point_size=args.point_size,
        lookback_bars=args.lookback_bars,
    )
    base = EAParameters()
    candidates = candidate_grid(base, wide=args.wide_grid)
    print(
        f"Learning candidates: {len(candidates)} | "
        f"risk={base.risk_percent:.2f}% FROZEN | daily loss={base.max_daily_loss_percent:.2f}% FROZEN"
    )
    result = run_learning(
        candles,
        decisions,
        base_params=base,
        candidates=candidates,
        point_size=args.point_size,
        initial_balance=args.balance,
        daily_goal_usd=args.daily_goal,
        train_days=args.train_days,
        test_days=args.test_days,
        min_train_trades=args.min_train_trades,
    )
    report = render_learning_report(
        result,
        train_days=args.train_days,
        test_days=args.test_days,
    )
    print(report, end="")

    prefix = args.output_prefix
    folds_path = prefix.with_name(prefix.name + "_folds.csv")
    trades_path = prefix.with_name(prefix.name + "_oos_trades.csv")
    candidate_path = prefix.with_name(prefix.name + "_candidate.json")
    report_path = prefix.with_name(prefix.name + "_report.txt")
    write_folds(folds_path, result.folds)
    write_trade_journal(trades_path, result.oos_trades)
    write_candidate(
        candidate_path,
        result,
        train_days=args.train_days,
        test_days=args.test_days,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"Fold results: {folds_path}")
    print(f"OOS trades:   {trades_path}")
    print(f"Candidate:    {candidate_path}")
    print(f"Report:       {report_path}")


if __name__ == "__main__":
    main()
