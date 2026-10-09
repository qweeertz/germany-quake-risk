import numpy as np
import pytest

from quakerisk.engine import GRADE_NAMES, OCCUPANCIES


def test_summary_is_consistent(dueren):
    s = dueren.summary()
    table = dueren.cell_table()
    assert s["loss_usd"] == pytest.approx(table.loss.sum())
    assert sum(s["loss_by_component_usd"].values()) == pytest.approx(s["loss_usd"])
    assert sum(s["loss_by_occupancy_usd"].values()) == pytest.approx(s["loss_usd"])
    assert sum(d["loss"] for d in s["by_state"]) == pytest.approx(s["loss_usd"], rel=1e-3)
    assert 0 < s["loss_ratio"] < 1
    assert 0 < s["fatalities"] < 1e5
    assert (table.loss <= table.value + 1).all()


def test_damage_grades_are_exclusive_bands(dueren):
    for occupancy in OCCUPANCIES:
        assert (dueren.results[occupancy]["grades"] >= -1e-6).all()
    grades = dueren.summary()["buildings_by_grade"]
    assert set(grades) == set(GRADE_NAMES)


@pytest.mark.parametrize("gmpe", ["ensemble", "eshm20", "akkar"])
def test_loss_increases_with_magnitude(engine, gmpe):
    losses = [engine.run(10.0, 51.0, m, depth=10, strike=0, gmpe=gmpe).summary()["loss_usd"] for m in (5.0, 5.5, 6.0, 6.5)]
    assert all(b > a for a, b in zip(losses, losses[1:]))


def test_eshm20_gives_higher_loss_than_the_ensemble(engine):
    ensemble = engine.run(6.5, 50.8, 6.0, depth=10, strike=135, gmpe="ensemble").summary()["loss_usd"]
    eshm20 = engine.run(6.5, 50.8, 6.0, depth=10, strike=135, gmpe="eshm20").summary()["loss_usd"]
    assert eshm20 > ensemble


def test_largest_magnitude_runs(engine):
    result = engine.run(10.0, 51.0, 8.0, depth=10, strike=135)
    table = result.cell_table()
    assert not table.isna().any().any()
    assert result.summary()["loss_usd"] > 1e10


def test_hexagon_layers_are_aligned(dueren):
    for resolution in (6, 7):
        layer = dueren.hex_layers(resolution)
        n = len(layer["ids"])
        assert n > 0 and all(len(layer[k]) == n for k in ("mmi", "pga", "loss", "fat", "value"))
        assert np.isclose(sum(layer["loss"]) * 1e6, dueren.summary()["loss_usd"], rtol=0.02)
