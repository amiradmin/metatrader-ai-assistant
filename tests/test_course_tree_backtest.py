from datetime import datetime, timedelta

from meta_trader_ai.backtest import Candle
from meta_trader_ai.course_tree_backtest import compare_course_tree


def test_course_tree_backtest_compares_identical_history() -> None:
    start = datetime(2026, 1, 1, 0, 0)
    candles: list[Candle] = []
    price = 4300.0
    for index in range(140):
        open_price = price
        close_price = price + 0.8
        candles.append(
            Candle(
                time=start + timedelta(minutes=15 * index),
                open=open_price,
                high=close_price + 0.3,
                low=open_price - 0.2,
                close=close_price,
                tick_volume=100,
                spread_points=20.0,
                real_volume=0,
            )
        )
        price = close_price

    pairs, blocked = compare_course_tree(
        candles,
        symbol="XAUUSD_o",
        point_size=0.01,
        stop_loss_points=300.0,
        take_profit_points=600.0,
        thresholds=[70, 75],
        lookback_bars=100,
    )

    assert len(pairs) == 2
    assert blocked >= 0
    for baseline, course in pairs:
        assert baseline.threshold == course.threshold
        assert baseline.trades >= 0
        assert course.trades >= 0
