"""Event loss table: the loss of every event of a stochastic event set, computed on the 1 km exposure.

Each event becomes a finite rupture at the sampled hypocentre. ESHM20 gives no orientation for its point sources
(strike 0, dip 90 are placeholders), so the strike is drawn uniformly (no preferred direction, seeded by the event id) and
the dip follows the rake: strike-slip 90, normal 60, thrust 40 degrees. Events are processed in parallel.
"""
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

from .engine import QuakeEngine
from .eventset import read_events
from .paths import HAZARD, PROCESSED

MIN_DEPTH_KM = 3.0  # shallower hypocentres would put the rupture above the ground
CHUNK = 100
_engine = None


def elt_path(name, gmpe):
    return HAZARD / f"elt_{name}_{gmpe}.parquet"


def mechanism(rake):
    """Dip (degrees) of the fault for a given rake."""
    if abs(rake + 90) <= 30:
        return 60.0
    if abs(rake - 90) <= 30:
        return 40.0
    return 90.0


def aal_path(name, gmpe):
    return HAZARD / f"aal_{name}_{gmpe}.parquet"


def _init_worker():
    global _engine
    _engine = QuakeEngine()


def _process(chunk, gmpe):
    """Loss totals of the events of a chunk, and their summed loss per 1 km cell (residential, commercial,
    industrial, fatalities)."""
    out = []
    per_cell = {key: np.zeros(len(_engine.cells)) for key in ("res", "com", "ind", "fat")}
    for e in chunk:
        strike = float(np.random.default_rng(int(e["event_id"])).uniform(0, 180))
        dip, depth = mechanism(e["rake"]), max(float(e["depth"]), MIN_DEPTH_KM)
        result = _engine.run(e["lon"], e["lat"], float(e["mag"]), depth, strike, dip, float(e["rake"]), gmpe=gmpe)
        out.append({"event_id": e["event_id"], "strike": strike, "dip": dip, **result.totals()})
        for occupancy in ("res", "com", "ind"):
            r = result.results[occupancy]
            per_cell[occupancy][result.cells] += r["structural"] + r["nonstructural"] + r["contents"]
        per_cell["fat"][result.cells] += result.results["res"]["fatalities"]
    return out, per_cell


def compute(name: str = "eventset", gmpe: str = "ensemble", workers: int | None = None) -> pd.DataFrame:
    events, meta = read_events(name)
    records = events.to_dict("records")
    chunks = [records[i:i + CHUNK] for i in range(0, len(records), CHUNK)]
    workers = workers or max(1, min(12, (os.cpu_count() or 2) - 2))
    print(f"{len(records)} events, ground motion '{gmpe}', {workers} workers")
    started, results = time.time(), []
    cells = pd.read_parquet(PROCESSED / "cells.parquet", columns=["id", "lon", "lat", "state"])
    total = {key: np.zeros(len(cells)) for key in ("res", "com", "ind", "fat")}
    with ProcessPoolExecutor(workers, initializer=_init_worker) as pool:
        futures = [pool.submit(_process, c, gmpe) for c in chunks]
        for future in as_completed(futures):
            records_done, per_cell = future.result()
            results.extend(records_done)
            for key in total:
                total[key] += per_cell[key]
            if (len(results) // CHUNK) % 10 == 0:
                rate = len(results) / (time.time() - started)
                print(f"  {len(results)}/{len(records)} events, {rate:.1f} events/s, "
                      f"about {(len(records) - len(results)) / rate / 60:.0f} min left")
    elt = events.merge(pd.DataFrame(results), on="event_id").sort_values("event_id").reset_index(drop=True)
    HAZARD.mkdir(parents=True, exist_ok=True)
    elt.to_parquet(elt_path(name, gmpe), index=False)
    years = meta["years"]  # expected annual loss per cell = summed loss over all events / simulated years
    aal = cells.assign(aal_res=total["res"] / years, aal_com=total["com"] / years, aal_ind=total["ind"] / years,
                       aal_fatalities=total["fat"] / years)
    aal["aal"] = aal.aal_res + aal.aal_com + aal.aal_ind
    aal.to_parquet(aal_path(name, gmpe), index=False)
    print(f"done in {(time.time() - started) / 60:.1f} min: {len(elt)} events, "
          f"total loss {elt.loss.sum() / 1e9:.1f} bn USD over {meta['years']} years")
    return elt
