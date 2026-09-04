"""Temporal walk-forward validation for the four Anti-Chase research candidates.

Research only. This module never edits the live EA and never places orders.
The candidate set is intentionally frozen to four variants after the parameter
sweep: current E1.50, E3.25, E3.75 and no Anti-Chase. Risk, RR, spread and
daily-loss controls remain frozen.

Important: E3.25/E3.75 were discovered on overlapping historical data, so this
is a temporal stability / pseudo-OOS check, not pristine out-of-sample proof.
Only future forward-demo data can provide a genuinely untouched holdout.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from meta_trader_ai.backtest import Candle, load_candles
from meta_trader_ai.ea_m1_simulator import simulate_m15_signals_on_m1
from meta_trader_ai.ea_simulator import (
    EAParameters,
    SimulationMetrics,
    SimulatedTrade,
    _calculate_metrics,
    generate_decisions,
    write_trade_journal,
)
from meta_trader_ai.simulate_yesterday import _find_history_files


@dataclass(frozen=True, slots=True)
class Candidate:
    code: str
    label: str
    params: EAParameters


@dataclass(frozen=True, slots=True)
class FoldWindow:
    fold: int
    train_dates: tuple[date, ...]
    test_dates: tuple[date, ...]


@dataclass(frozen=True, slots=True)
class FoldResult:
    fold: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    selected_code: str
    fallback: bool
    train_trades: int
    train_pf: float
    train_expectancy_r: float
    train_score: float
    test_trades: int
    test_pf: float
    test_expectancy_r: float
    test_net_r: float
    test_dd_r: float


@dataclass(slots=True)
class AggregateState:
    balance: float
    trades: list[SimulatedTrade] = field(default_factory=list)
    daily_pnl: defaultdict[date, float] = field(default_factory=lambda: defaultdict(float))
    dates: list[date] = field(default_factory=list)
    fold_net_r: list[float] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CandidateSummary:
    code: str
    metrics: SimulationMetrics
    positive_folds: int
    total_folds: int
    promotion_gate: bool


def build_candidates() -> list[Candidate]:
    """Return the frozen four-way comparison; no parameter mining occurs here."""
    base = EAParameters(
        min_confidence=75,
        risk_percent=0.5,
        reward_risk_ratio=2.0,
        max_spread_points=50.0,
        max_spread_atr_ratio=0.25,
        max_daily_loss_percent=1.5,
        use_anti_chase=True,
        max_extension_atr=1.5,
        pullback_zone_atr=0.35,
        pullback_max_bars=4,
    )
    return [
        Candidate("CURRENT_E1.50", "Current E1.50", base),
        Candidate(
            "E3.25_Z0.35_W4",
            "E3.25 / zone 0.35 / wait 4",
            replace(base, max_extension_atr=3.25),
        ),
        Candidate(
            "E3.75_Z0.35_W4",
            "E3.75 / zone 0.35 / wait 4",
            replace(base, max_extension_atr=3.75),
        ),
        Candidate(
            "NO_ANTI_CHASE",
            "No Anti-Chase benchmark",
            replace(base, use_anti_chase=False),
        ),
    ]


def build_folds(
    dates: list[date], *, train_days: int, test_days: int
) -> list[FoldWindow]:
    if train_days < 10:
        raise ValueError("train_days must be >= 10")
    if test_days < 5:
        raise ValueError("test_days must be >= 5")
    if len(dates) < train_days + test_days:
        raise ValueError(
            f"Need at least {train_days + test_days} clean trading dates; got {len(dates)}"
        )

    folds: list[FoldWindow] = []
    position = train_days
    fold_no = 0
    while position + test_days <= len(dates):
        fold_no += 1
        train = tuple(dates[position - train_days : position])
        test = tuple(dates[position : position + test_days])
        folds.append(FoldWindow(fold_no, train, test))
        position += test_days
    if not folds:
        raise ValueError("No complete walk-forward fold can be built")
    return folds


def _clean_dates(
    m1: list[Candle], *, days: int | None, include_latest: bool
) -> tuple[list[date], list[str]]:
    if not m1:
        raise ValueError("M1 history is empty")
    dates = sorted({item.time.date() for item in m1})
    notes: list[str] = []

    if not include_latest and dates:
        notes.append(f"Excluded newest date {dates[-1]} (may be incomplete).")
        dates = dates[:-1]

    # MT5 history exports can start in the middle of the oldest stored day.
    # Exclude it when the first candle is clearly intraday (> 03:00 broker time).
    if dates and m1[0].time.date() == dates[0] and m1[0].time.hour >= 3:
        notes.append(
            f"Excluded oldest date {dates[0]} because history starts intraday at {m1[0].time.time()}."
        )
        dates = dates[1:]

    if days is not None:
        if days < 1:
            raise ValueError("--days must be positive")
        dates = dates[-days:]
    if not dates:
        raise ValueError("No clean trading dates remain")
    return dates, notes


def _window_indices(m1: list[Candle], first_day: date, last_day: date) -> tuple[int, int]:
    start = next(i for i, candle in enumerate(m1) if candle.time.date() >= first_day)
    end = next(
        i
        for i in range(len(m1) - 1, -1, -1)
        if m1[i].time.date() <= last_day
    )
    return start, end


def selection_score(metrics: SimulationMetrics, *, min_train_trades: int) -> float:
    """Rank train-window evidence while punishing tiny samples and drawdown."""
    if metrics.trades < min_train_trades:
        return -math.inf
    pf = 3.0 if math.isinf(metrics.profit_factor) else min(metrics.profit_factor, 3.0)
    return (
        metrics.expectancy_r * math.sqrt(metrics.trades)
        + 0.10 * pf
        - 0.15 * metrics.max_drawdown_r
    )


def _append_result(state: AggregateState, result, test_dates: tuple[date, ...]) -> None:
    state.balance = result.metrics.end_balance
    state.trades.extend(result.trades)
    state.dates.extend(test_dates)
    state.fold_net_r.append(result.metrics.net_r)
    for day, pnl in result.daily_pnl.items():
        state.daily_pnl[day] += pnl


def _aggregate_metrics(state: AggregateState, *, initial_balance: float) -> SimulationMetrics:
    dates = sorted(set(state.dates))
    for day in dates:
        state.daily_pnl.setdefault(day, 0.0)
    return _calculate_metrics(
        state.trades,
        start_balance=initial_balance,
        end_balance=state.balance,
        trading_dates=dates,
        daily_pnl=dict(state.daily_pnl),
        daily_goal_usd=10.0,
    )


def _gate(metrics: SimulationMetrics, positive_folds: int, total_folds: int) -> bool:
    pf = metrics.profit_factor
    positive_share = positive_folds / total_folds if total_folds else 0.0
    return (
        metrics.trades >= 10
        and metrics.expectancy_r > 0.0
        and (math.isinf(pf) or pf >= 1.20)
        and metrics.max_drawdown_r <= 5.0
        and positive_share >= 0.60
    )


def run_walk_forward(
    m15: list[Candle],
    m1: list[Candle],
    *,
    dates: list[date],
    train_days: int,
    test_days: int,
    min_train_trades: int,
    initial_balance: float,
) -> tuple[list[FoldResult], list[CandidateSummary], SimulationMetrics, list[SimulatedTrade]]:
    candidates = build_candidates()
    folds = build_folds(dates, train_days=train_days, test_days=test_days)
    decisions = generate_decisions(
        m15,
        symbol="XAUUSD_o",
        point_size=0.01,
        lookback_bars=100,
    )

    states = {c.code: AggregateState(initial_balance) for c in candidates}
    selected_state = AggregateState(initial_balance)
    fold_results: list[FoldResult] = []

    for fold in folds:
        train_start, train_end = _window_indices(m1, fold.train_dates[0], fold.train_dates[-1])
        test_start, test_end = _window_indices(m1, fold.test_dates[0], fold.test_dates[-1])

        train_rank: list[tuple[float, Candidate, SimulationMetrics]] = []
        for candidate in candidates:
            train_result = simulate_m15_signals_on_m1(
                m15,
                m1,
                decisions,
                params=candidate.params,
                point_size=0.01,
                initial_balance=initial_balance,
                daily_goal_usd=10.0,
                m1_start_index=train_start,
                m1_end_index=train_end,
            )
            score = selection_score(
                train_result.metrics, min_train_trades=min_train_trades
            )
            train_rank.append((score, candidate, train_result.metrics))

        eligible_train = [item for item in train_rank if math.isfinite(item[0])]
        fallback = not eligible_train
        if eligible_train:
            score, selected, selected_train_metrics = max(
                eligible_train,
                key=lambda item: (
                    item[0],
                    -item[2].max_drawdown_r,
                    item[2].trades,
                ),
            )
        else:
            # Conservative fallback: do not promote a mined candidate from a tiny
            # train sample. Keep the current production guard for this fold.
            selected = candidates[0]
            selected_train_metrics = next(
                metrics for _, candidate, metrics in train_rank if candidate.code == selected.code
            )
            score = -math.inf

        # Evaluate every frozen candidate on the same non-overlapping test fold.
        test_by_code = {}
        for candidate in candidates:
            state = states[candidate.code]
            result = simulate_m15_signals_on_m1(
                m15,
                m1,
                decisions,
                params=candidate.params,
                point_size=0.01,
                initial_balance=state.balance,
                daily_goal_usd=10.0,
                m1_start_index=test_start,
                m1_end_index=test_end,
            )
            _append_result(state, result, fold.test_dates)
            test_by_code[candidate.code] = result

        selected_test = simulate_m15_signals_on_m1(
            m15,
            m1,
            decisions,
            params=selected.params,
            point_size=0.01,
            initial_balance=selected_state.balance,
            daily_goal_usd=10.0,
            m1_start_index=test_start,
            m1_end_index=test_end,
        )
        _append_result(selected_state, selected_test, fold.test_dates)

        fold_results.append(
            FoldResult(
                fold=fold.fold,
                train_start=fold.train_dates[0],
                train_end=fold.train_dates[-1],
                test_start=fold.test_dates[0],
                test_end=fold.test_dates[-1],
                selected_code=selected.code,
                fallback=fallback,
                train_trades=selected_train_metrics.trades,
                train_pf=selected_train_metrics.profit_factor,
                train_expectancy_r=selected_train_metrics.expectancy_r,
                train_score=score,
                test_trades=selected_test.metrics.trades,
                test_pf=selected_test.metrics.profit_factor,
                test_expectancy_r=selected_test.metrics.expectancy_r,
                test_net_r=selected_test.metrics.net_r,
                test_dd_r=selected_test.metrics.max_drawdown_r,
            )
        )

    summaries: list[CandidateSummary] = []
    for candidate in candidates:
        state = states[candidate.code]
        metrics = _aggregate_metrics(state, initial_balance=initial_balance)
        positive = sum(value > 0.0 for value in state.fold_net_r)
        summaries.append(
            CandidateSummary(
                candidate.code,
                metrics,
                positive,
                len(state.fold_net_r),
                _gate(metrics, positive, len(state.fold_net_r)),
            )
        )

    selected_metrics = _aggregate_metrics(selected_state, initial_balance=initial_balance)
    return fold_results, summaries, selected_metrics, selected_state.trades


def _pf(value: float) -> str:
    return "inf" if math.isinf(value) else f"{value:.2f}"


def render_report(
    folds: list[FoldResult],
    summaries: list[CandidateSummary],
    selected_metrics: SimulationMetrics,
    *,
    dates: list[date],
    train_days: int,
    test_days: int,
    min_train_trades: int,
    notes: list[str],
) -> str:
    lines = [
        "META TRADER AI - ANTI-CHASE TEMPORAL WALK-FORWARD",
        "=" * 118,
        f"Clean date range: {dates[0]} -> {dates[-1]} | dates={len(dates)}",
        f"Design: rolling train {train_days} days -> next {test_days} unseen days | folds={len(folds)}",
        f"Train selection requires >= {min_train_trades} trades; otherwise CURRENT_E1.50 safety fallback.",
        "Frozen: confidence=75, risk=0.5%, RR=2.0, spread guards, daily-loss guard and signal engine.",
        "Candidates only: CURRENT_E1.50, E3.25_Z0.35_W4, E3.75_Z0.35_W4, NO_ANTI_CHASE.",
        "WARNING: E3.25/E3.75 were discovered on overlapping history; this is temporal stability, not pristine OOS proof.",
        "Historical news/TipRanks are not reconstructed; M1 execution is pseudo-tick, not tick-perfect.",
    ]
    lines.extend(f"NOTE: {item}" for item in notes)
    lines.extend(
        [
            "",
            "FOLDS (train winner -> next unseen test window)",
            f"{'#':>2} {'TRAIN':<23} {'TEST':<23} {'SELECTED':<20} {'TRN':>3} {'TR_E':>7} {'TR_PF':>6} {'TST':>3} {'TS_E':>7} {'TS_PF':>6} {'NETR':>6} {'DD':>5} {'FB':>2}",
            "-" * 118,
        ]
    )
    for fold in folds:
        train = f"{fold.train_start}->{fold.train_end}"
        test = f"{fold.test_start}->{fold.test_end}"
        lines.append(
            f"{fold.fold:>2} {train:<23} {test:<23} {fold.selected_code:<20} "
            f"{fold.train_trades:>3} {fold.train_expectancy_r:>+7.3f} {_pf(fold.train_pf):>6} "
            f"{fold.test_trades:>3} {fold.test_expectancy_r:>+7.3f} {_pf(fold.test_pf):>6} "
            f"{fold.test_net_r:>+6.2f} {fold.test_dd_r:>5.2f} {'Y' if fold.fallback else '-':>2}"
        )

    lines.extend(
        [
            "",
            "FIXED-CANDIDATE TEST-FOLD AGGREGATES",
            f"{'CANDIDATE':<22} {'TRD':>4} {'WR%':>6} {'PF':>6} {'E(R)':>8} {'NETR':>7} {'DD':>6} {'$/D':>8} {'POSF':>6} {'GATE':>5}",
            "-" * 92,
        ]
    )
    for item in summaries:
        m = item.metrics
        lines.append(
            f"{item.code:<22} {m.trades:>4} {m.win_rate:>5.1f}% {_pf(m.profit_factor):>6} "
            f"{m.expectancy_r:>+8.3f} {m.net_r:>+7.2f} {m.max_drawdown_r:>6.2f} "
            f"{m.average_daily_pnl_usd:>+8.2f} {item.positive_folds:>2}/{item.total_folds:<3} "
            f"{'YES' if item.promotion_gate else 'no':>5}"
        )

    m = selected_metrics
    lines.extend(
        [
            "",
            "ROLLING TRAIN-SELECTED PATH (research only)",
            f"  Trades={m.trades} | WR={m.win_rate:.1f}% | PF={_pf(m.profit_factor)} | E={m.expectancy_r:+.3f}R",
            f"  Net={m.net_r:+.2f}R | DD={m.max_drawdown_r:.2f}R | avg={m.average_daily_pnl_usd:+.2f}$/day",
            "",
            "Promotion gate for a fixed candidate: >=10 test trades, PF>=1.20, E>0, DD<=5R, positive in >=60% of test folds.",
            "Passing this gate means SHADOW candidate only; it never changes the live/demo EA automatically.",
            "A truly untouched OOS decision still requires future forward-demo data after 2026-09-04.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_folds(path: Path, folds: list[FoldResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FoldResult.__dataclass_fields__))
        writer.writeheader()
        for fold in folds:
            writer.writerow({name: getattr(fold, name) for name in FoldResult.__dataclass_fields__})


def write_summaries(path: Path, summaries: list[CandidateSummary]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "code", "trades", "wins", "losses", "win_rate", "profit_factor",
            "expectancy_r", "net_r", "net_usd", "max_drawdown_r",
            "avg_daily_usd", "positive_folds", "total_folds", "promotion_gate",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in summaries:
            m = item.metrics
            writer.writerow(
                {
                    "code": item.code,
                    "trades": m.trades,
                    "wins": m.wins,
                    "losses": m.losses,
                    "win_rate": m.win_rate,
                    "profit_factor": m.profit_factor,
                    "expectancy_r": m.expectancy_r,
                    "net_r": m.net_r,
                    "net_usd": m.net_usd,
                    "max_drawdown_r": m.max_drawdown_r,
                    "avg_daily_usd": m.average_daily_pnl_usd,
                    "positive_folds": item.positive_folds,
                    "total_folds": item.total_folds,
                    "promotion_gate": item.promotion_gate,
                }
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m15-csv", type=Path)
    parser.add_argument("--m1-csv", type=Path)
    parser.add_argument("--days", type=int, help="Use only the newest N clean trading dates.")
    parser.add_argument("--train-days", type=int, default=30)
    parser.add_argument("--test-days", type=int, default=10)
    parser.add_argument("--min-train-trades", type=int, default=3)
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--include-latest", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.min_train_trades < 1:
        raise SystemExit("--min-train-trades must be positive")

    if args.m15_csv and args.m1_csv:
        m15_path, m1_path = args.m15_csv, args.m1_csv
        output_dir = args.output_dir or Path("data")
    elif args.m15_csv or args.m1_csv:
        raise SystemExit("Pass both --m15-csv and --m1-csv, or neither for MT5 auto-detection.")
    else:
        m15_path, m1_path, files_dir = _find_history_files()
        output_dir = args.output_dir or files_dir

    m15 = load_candles(m15_path)
    m1 = load_candles(m1_path)
    dates, notes = _clean_dates(m1, days=args.days, include_latest=args.include_latest)

    folds, summaries, selected_metrics, selected_trades = run_walk_forward(
        m15,
        m1,
        dates=dates,
        train_days=args.train_days,
        test_days=args.test_days,
        min_train_trades=args.min_train_trades,
        initial_balance=args.balance,
    )
    report = render_report(
        folds,
        summaries,
        selected_metrics,
        dates=dates,
        train_days=args.train_days,
        test_days=args.test_days,
        min_train_trades=args.min_train_trades,
        notes=notes,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    folds_path = output_dir / "anti_chase_walk_forward_folds.csv"
    candidates_path = output_dir / "anti_chase_walk_forward_candidates.csv"
    trades_path = output_dir / "anti_chase_walk_forward_selected_trades.csv"
    report_path = output_dir / "anti_chase_walk_forward_report.txt"
    write_folds(folds_path, folds)
    write_summaries(candidates_path, summaries)
    write_trade_journal(trades_path, selected_trades)
    report_path.write_text(report, encoding="utf-8")

    print(report, end="")
    print(f"\nFolds CSV:      {folds_path}")
    print(f"Candidates CSV: {candidates_path}")
    print(f"Selected trades:{trades_path}")
    print(f"Text report:    {report_path}")
    print("LIVE EA: unchanged. Walk-forward is research-only.")


if __name__ == "__main__":
    main()
