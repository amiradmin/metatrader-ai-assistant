from meta_trader_ai.compare_yesterday import _scenarios


def test_yesterday_comparison_only_changes_confidence_and_anti_chase() -> None:
    scenarios = _scenarios()
    assert [item.code for item in scenarios] == ["A", "B", "C", "D"]

    for item in scenarios:
        params = item.params
        assert params.risk_percent == 0.5
        assert params.reward_risk_ratio == 2.0
        assert params.max_spread_points == 50.0
        assert params.max_spread_atr_ratio == 0.25
        assert params.max_daily_loss_percent == 1.5
        assert params.use_anti_chase is True

    assert scenarios[0].params.min_confidence == 75
    assert scenarios[1].params.min_confidence == 70
    assert scenarios[2].params.min_confidence == 75
    assert scenarios[3].params.min_confidence == 70

    assert scenarios[0].params.max_extension_atr == 1.5
    assert scenarios[0].params.pullback_zone_atr == 0.35
    assert scenarios[0].params.pullback_max_bars == 4

    for item in scenarios[2:]:
        assert item.params.max_extension_atr == 2.0
        assert item.params.pullback_zone_atr == 0.50
        assert item.params.pullback_max_bars == 6
