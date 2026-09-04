from datetime import date, timedelta
from types import SimpleNamespace

from meta_trader_ai.anti_chase_walk_forward import (
    build_candidates,
    build_folds,
    selection_score,
)


def test_walk_forward_candidate_set_is_frozen_and_safe() -> None:
    candidates = build_candidates()
    assert [item.code for item in candidates] == [
        "CURRENT_E1.50",
        "E3.25_Z0.35_W4",
        "E3.75_Z0.35_W4",
        "NO_ANTI_CHASE",
    ]
    for item in candidates:
        assert item.params.min_confidence == 75
        assert item.params.risk_percent == 0.5
        assert item.params.reward_risk_ratio == 2.0
        assert item.params.max_daily_loss_percent == 1.5
    assert candidates[0].params.max_extension_atr == 1.5
    assert candidates[1].params.max_extension_atr == 3.25
    assert candidates[2].params.max_extension_atr == 3.75
    assert candidates[3].params.use_anti_chase is False


def test_walk_forward_folds_use_non_overlapping_future_test_windows() -> None:
    start = date(2026, 1, 1)
    dates = [start + timedelta(days=i) for i in range(70)]
    folds = build_folds(dates, train_days=30, test_days=10)
    assert len(folds) == 4
    assert folds[0].train_dates == tuple(dates[:30])
    assert folds[0].test_dates == tuple(dates[30:40])
    assert folds[1].train_dates == tuple(dates[10:40])
    assert folds[1].test_dates == tuple(dates[40:50])
    all_test_dates = [day for fold in folds for day in fold.test_dates]
    assert len(all_test_dates) == len(set(all_test_dates)) == 40


def test_selection_score_rejects_tiny_train_samples() -> None:
    tiny = SimpleNamespace(
        trades=2,
        expectancy_r=1.0,
        profit_factor=5.0,
        max_drawdown_r=0.0,
    )
    enough = SimpleNamespace(
        trades=3,
        expectancy_r=0.2,
        profit_factor=1.4,
        max_drawdown_r=1.0,
    )
    assert selection_score(tiny, min_train_trades=3) == float("-inf")
    assert selection_score(enough, min_train_trades=3) > float("-inf")
