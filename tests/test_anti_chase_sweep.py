from meta_trader_ai.anti_chase_sweep import (
    DEFAULT_EXTENSIONS,
    DEFAULT_WAITS,
    DEFAULT_ZONES,
    _neighbor_robustness,
    build_grid,
)


def test_default_anti_chase_grid_has_28_controlled_configs() -> None:
    configs = build_grid()
    assert len(configs) == len(DEFAULT_EXTENSIONS) * len(DEFAULT_ZONES) * len(DEFAULT_WAITS) == 28
    assert configs[0].max_extension_atr == 1.90
    assert configs[-1].max_extension_atr == 3.00
    assert {item.pullback_zone_atr for item in configs} == {0.35, 0.50}
    assert {item.pullback_max_bars for item in configs} == {4, 6}


def test_neighbor_robustness_rewards_stable_positive_neighborhood() -> None:
    rows = []
    for extension in (2.0, 2.5, 3.0):
        rows.append(
            {
                "max_extension_atr": extension,
                "pullback_zone_atr": 0.35,
                "pullback_max_bars": 4,
                "expectancy_r": 0.2,
                "profit_factor": 1.3,
                "robust_neighbors_percent": 0.0,
            }
        )

    _neighbor_robustness(rows)

    assert rows[0]["robust_neighbors_percent"] == 100.0
    assert rows[1]["robust_neighbors_percent"] == 100.0
    assert rows[2]["robust_neighbors_percent"] == 100.0
