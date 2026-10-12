import numpy as np
import pytest

from quakerisk import eventloss
from quakerisk.eventset import keep_distance_km


def test_distance_limits_grow_with_magnitude():
    limits = keep_distance_km(np.array([4.6, 5.4, 5.6, 6.4, 6.6, 7.5]))
    assert limits.tolist() == [150, 150, 300, 300, 400, 400]


@pytest.mark.parametrize("rake, dip", [(0, 90), (-90, 60), (-70, 60), (90, 40), (180, 90)])
def test_dip_follows_the_mechanism(rake, dip):
    assert eventloss.mechanism(rake) == dip


def test_event_losses_add_up_to_the_per_cell_losses():
    events = [
        {"event_id": 1, "lon": 6.5, "lat": 50.8, "mag": 5.5, "depth": 8.0, "rake": 0.0},
        {"event_id": 2, "lon": 9.0, "lat": 48.3, "mag": 5.0, "depth": 2.0, "rake": -90.0},  # shallow: depth is raised
        {"event_id": 3, "lon": 2.0, "lat": 44.0, "mag": 5.0, "depth": 8.0, "rake": 0.0},  # too far from every cell
    ]
    eventloss._init_worker()
    records, per_cell = eventloss._process(events, "ensemble")
    assert [r["event_id"] for r in records] == [1, 2, 3]
    assert records[2]["loss"] == 0 and records[2]["n_cells"] == 0
    assert records[0]["loss"] > records[1]["loss"] > 0
    per_cell_total = per_cell["res"].sum() + per_cell["com"].sum() + per_cell["ind"].sum()
    assert per_cell_total == pytest.approx(sum(r["loss"] for r in records))
    # the strike is drawn from the event id, so the result is reproducible
    again, _ = eventloss._process(events[:1], "ensemble")
    assert again[0]["loss"] == pytest.approx(records[0]["loss"])
