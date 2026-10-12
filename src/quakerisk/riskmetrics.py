"""Risk metrics from an event loss table: average annual loss and exceedance-probability curves.

Years are the simulated years of the event set (years without events have zero loss). Standard errors come from a
bootstrap over years. A return period is only reliable if it is well below the number of simulated years (about a tenth).
"""
import json

import numpy as np
import pandas as pd

from .eventloss import elt_path
from .eventset import read_events
from .paths import HAZARD

RETURN_PERIODS = [2, 5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000]
BOOTSTRAPS = 200


def metrics_path(name, gmpe):
    return HAZARD / f"metrics_{name}_{gmpe}.json"


def annual_losses(elt: pd.DataFrame, years: int):
    """Annual aggregate (AEP basis) and annual maximum event (OEP basis) loss for every simulated year."""
    per_year = elt.groupby("year").loss.agg(["sum", "max"]).reindex(range(1, years + 1), fill_value=0.0)
    return per_year["sum"].to_numpy(), per_year["max"].to_numpy()


def return_period_loss(annual: np.ndarray, period: float) -> float:
    """Loss exceeded with probability 1/period per year (Weibull plotting positions on the simulated years)."""
    ordered = np.sort(annual)[::-1]
    exceedance = np.arange(1, len(ordered) + 1) / (len(ordered) + 1)
    return float(np.interp(1.0 / period, exceedance, ordered))


def compute(name: str = "eventset", gmpe: str = "ensemble") -> dict:
    _, meta = read_events(name)
    years = meta["years"]
    elt = pd.read_parquet(elt_path(name, gmpe))
    aggregate, maximum = annual_losses(elt, years)
    rng = np.random.default_rng(1)
    boot = [rng.integers(0, years, years) for _ in range(BOOTSTRAPS)]

    out = {"event_set": name, "ground_motion": gmpe, "years": years, "events": int(len(elt)),
           "aal_usd": float(aggregate.mean()), "aal_std_error_usd": float(np.std([aggregate[i].mean() for i in boot])),
           "aal_fatalities": float(elt.fatalities.sum() / years),
           "aal_by_occupancy_usd": {o: float(elt[f"loss_{o}"].sum() / years) for o in ("res", "com", "ind")},
           "years_with_loss_above_1m_usd": float((aggregate > 1e6).mean()), "return_periods": []}
    for period in RETURN_PERIODS:
        row = {"return_period_years": period, "reliable": bool(period <= years / 10)}
        for key, series in (("oep_usd", maximum), ("aep_usd", aggregate)):
            row[key] = return_period_loss(series, period)
            row[key + "_std_error"] = float(np.std([return_period_loss(series[i], period) for i in boot]))
        out["return_periods"].append(row)
    HAZARD.mkdir(parents=True, exist_ok=True)
    metrics_path(name, gmpe).write_text(json.dumps(out, indent=1))
    print(f"average annual loss {out['aal_usd'] / 1e6:.0f} m USD (+-{out['aal_std_error_usd'] / 1e6:.0f}), "
          f"fatalities {out['aal_fatalities']:.2f} per year")
    print("return period | OEP (largest event of a year)      | AEP (sum of the year)")
    for r in out["return_periods"]:
        flag = "" if r["reliable"] else "  (too few simulated years)"
        print(f"  {r['return_period_years']:5d} yr   | {r['oep_usd'] / 1e9:8.2f} bn (+-{r['oep_usd_std_error'] / 1e9:5.2f})"
              f" | {r['aep_usd'] / 1e9:8.2f} bn (+-{r['aep_usd_std_error'] / 1e9:5.2f}){flag}")
    return out
