import json

import numpy as np
import pandas as pd
import pytest

from quakerisk import riskmetrics


def test_annual_losses_include_years_without_events():
    elt = pd.DataFrame({"year": [1, 1, 3], "loss": [10.0, 5.0, 7.0]})
    aggregate, maximum = riskmetrics.annual_losses(elt, years=4)
    assert aggregate.tolist() == [15.0, 0.0, 7.0, 0.0]
    assert maximum.tolist() == [10.0, 0.0, 7.0, 0.0]


def test_return_period_loss_of_a_known_series():
    annual = np.arange(1, 1001, dtype=float)  # 1000 simulated years with losses 1 ... 1000
    assert riskmetrics.return_period_loss(annual, 10) == pytest.approx(900, abs=2)
    assert riskmetrics.return_period_loss(annual, 100) == pytest.approx(990, abs=2)
    assert riskmetrics.return_period_loss(annual, 2) == pytest.approx(500, abs=2)


def test_compute_writes_average_annual_loss(tmp_path, monkeypatch):
    years = 2000
    rng = np.random.default_rng(0)
    elt = pd.DataFrame({"year": rng.integers(1, years + 1, 500), "loss": rng.lognormal(10, 1, 500)})
    elt["loss_res"], elt["loss_com"], elt["loss_ind"] = 0.6 * elt.loss, 0.3 * elt.loss, 0.1 * elt.loss
    elt["fatalities"] = 0.001 * elt.loss
    monkeypatch.setattr(riskmetrics, "HAZARD", tmp_path)
    monkeypatch.setattr(riskmetrics, "elt_path", lambda name, gmpe: tmp_path / "elt.parquet")
    monkeypatch.setattr(riskmetrics, "read_events", lambda name: (None, {"years": years}))
    elt.to_parquet(tmp_path / "elt.parquet")
    out = riskmetrics.compute("test", "ensemble")
    assert out["aal_usd"] == pytest.approx(elt.loss.sum() / years)
    assert sum(out["aal_by_occupancy_usd"].values()) == pytest.approx(out["aal_usd"])
    reliable = [r["return_period_years"] for r in out["return_periods"] if r["reliable"]]
    assert max(reliable) <= years / 10
    saved = json.loads((tmp_path / "metrics_test_ensemble.json").read_text())
    assert saved["years"] == years
