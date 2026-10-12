"""Consistency check of the stochastic event set: hazard computed from the events against OpenQuake's classical PSHA.

For every site the annual rate of exceeding a PGA level is summed over the events of the event set, with the complete
ESHM20 ground-motion tree (15 weighted branches of the Kotha et al. 2020 model, shallow default) and the same truncation
(3 sigma), site model (Vs30 800 m/s, attenuation region) and PGA levels as the classical calculation. Two rupture
treatments: "point" (distance = epicentral distance, as ESHM20 models its point sources) and "finite" (the planar
rupture of the loss engine with a random strike).

Usage:  python validation/check_event_set.py --events es20k --classical <OpenQuake calculation id> [--job es20k]
"""
import argparse
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
from openquake.commonlib import datastore
from openquake.hazardlib.contexts import simple_cmaker
from openquake.hazardlib.geo import Mesh
from openquake.hazardlib.geo.geodetic import geodetic_distance
from openquake.hazardlib.gsim.kotha_2020 import KothaEtAl2020ESHM20
from scipy.special import ndtr

from quakerisk.eventloss import mechanism
from quakerisk.eventset import read_events
from quakerisk.hazard import build_rupture
from quakerisk.paths import ESHM20_RAW, HAZARD, ROOT

MAX_DISTANCE_KM = 200.0  # the 'default' maximum distance of the classical calculation
TRUNCATION = 3.0


def ground_motion_branches():
    """(weight, sigma_mu_epsilon, c3_epsilon) of the shallow-default branches of the ESHM20 ground-motion tree."""
    text = (ESHM20_RAW / "gmpe_complete_logic_tree_5br.xml").read_text()
    shallow = text.split('branchSetID="Shallow_Def"')[1].split("</logicTreeBranchSet>")[0]
    branches = []
    for body in re.findall(r"<logicTreeBranch\b.*?</logicTreeBranch>", shallow, flags=re.S):
        weight = float(re.search(r"<uncertaintyWeight>\s*([\d.eE+-]+)", body).group(1))
        sigma_mu = re.search(r"sigma_mu_epsilon\s*=\s*([-\d.]+)", body)
        c3 = re.search(r"c3_epsilon\s*=\s*([-\d.]+)", body)
        branches.append((weight, float(sigma_mu.group(1)) if sigma_mu else 0.0, float(c3.group(1)) if c3 else 0.0))
    assert abs(sum(w for w, _, _ in branches) - 1) < 1e-6, "weights do not sum to one"
    return branches


def rjb_matrices(events, sites):
    """Joyner-Boore distance (km) of every event to every site, for a point and for a finite rupture."""
    lon, lat = sites.lon.to_numpy(), sites.lat.to_numpy()
    point = np.stack([geodetic_distance(e.lon, e.lat, lon, lat) for e in events.itertuples()]).astype(np.float32)
    finite = np.empty_like(point)
    mesh = Mesh(lon, lat)
    for i, e in enumerate(events.itertuples()):
        strike = float(np.random.default_rng(int(e.event_id)).uniform(0, 180))
        surface, _ = build_rupture(e.lon, e.lat, float(e.mag), max(float(e.depth), 3.0), strike, mechanism(e.rake), e.rake)
        finite[i] = surface.get_joyner_boore_distance(mesh)
    return point, finite


def event_set_hazard(events, sites, rjb, levels, years, branches):
    """Annual exceedance rate of each PGA level at each site, shape (sites, levels)."""
    makers = [(w, simple_cmaker([KothaEtAl2020ESHM20(sigma_mu_epsilon=s, c3_epsilon=c)], ["PGA"])) for w, s, c in branches]
    log_levels = np.log(levels)
    rates = np.zeros((len(sites), len(levels)))
    mag, depth = events.mag.to_numpy(), events.depth.to_numpy()
    for k, site in enumerate(sites.itertuples()):
        near = np.where(rjb[:, k] < MAX_DISTANCE_KM)[0]
        if len(near) == 0:
            continue
        for weight, maker in makers:
            ctx = maker.new_ctx(len(near))
            ctx.mag, ctx.hypo_depth, ctx.rjb = mag[near], depth[near], rjb[near, k].astype(float)
            ctx.vs30, ctx.vs30measured = np.full(len(near), site.vs30), np.full(len(near), bool(site.vs30measured))
            ctx.region = np.full(len(near), int(site.region))
            mean, sigma, _, _ = maker.get_mean_stds([ctx])
            z = (log_levels[None, :] - mean[0, 0][:, None]) / sigma[0, 0][:, None]  # (events, levels)
            z = np.clip(z, -TRUNCATION, TRUNCATION)
            low, high = ndtr(-TRUNCATION), ndtr(TRUNCATION)
            exceed = (high - ndtr(z)) / (high - low)
            rates[k] += weight * exceed.sum(0) / years
    return rates


def level_at_rate(levels, rates, target):
    """PGA at which the (monotonically decreasing) exceedance rate equals `target` (log-log interpolation)."""
    ok = rates > 0
    if ok.sum() < 2 or target > rates[ok].max() or target < rates[ok].min():
        return np.nan
    return float(np.exp(np.interp(np.log(target), np.log(rates[ok])[::-1], np.log(levels[ok])[::-1])))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--events", default="es20k")
    parser.add_argument("--classical", type=int, required=True, help="OpenQuake calculation id of the classical PSHA")
    parser.add_argument("--job", default="es20k", help="folder of the event-set job (its site model is reused)")
    parser.add_argument("--site-stride", type=int, default=6, help="use every n-th site (the full set is slow)")
    args = parser.parse_args()

    events, meta = read_events(args.events)
    years = meta["years"]
    sites = pd.read_csv(HAZARD / f"job_{args.job}" / "site_model.csv").reset_index(drop=True)
    ds = datastore.read(args.classical)
    levels = np.array(ds["oqparam"].imtls["PGA"])
    classical_sites = pd.DataFrame({"lon": ds["sitecol"].array["lon"], "lat": ds["sitecol"].array["lat"]})
    mean_curves = ds["hcurves-stats"][:][:, 0, 0, :]  # (sites, levels): probability of exceedance in 1 year
    classical = -np.log1p(-np.clip(mean_curves, 0, 1 - 1e-12))
    assert len(classical_sites) == len(sites)
    # align the classical sites with the site table by coordinates
    key = lambda d: (d.lon.round(3).astype(str) + "_" + d.lat.round(3).astype(str))  # noqa: E731
    position = pd.Series(np.arange(len(classical_sites)), index=key(classical_sites))
    classical = classical[position.reindex(key(sites)).to_numpy()]

    keep = np.arange(0, len(sites), args.site_stride)
    sites, classical = sites.iloc[keep].reset_index(drop=True), classical[keep]
    relevant = (levels >= 0.004) & (levels <= 1.5)  # PGA range that matters for the hazard around 475 years
    levels, classical = levels[relevant], classical[:, relevant]
    branches = ground_motion_branches()
    print(f"{len(events)} events, {years} years, {len(sites)} sites, {len(branches)} ground-motion branches")
    started = time.time()
    point, finite = rjb_matrices(events, sites)
    print(f"distances computed in {time.time() - started:.0f} s")
    results = {}
    for name, rjb in (("point", point), ("finite", finite)):
        started = time.time()
        results[name] = event_set_hazard(events, sites, rjb, levels, years, branches)
        print(f"hazard from the event set, {name} rupture: {time.time() - started:.0f} s")

    target = -np.log1p(-0.002103)  # 475-year hazard map level
    table = sites[["lon", "lat"]].copy()
    table["classical_475"] = [level_at_rate(levels, classical[k], target) for k in range(len(sites))]
    for name in results:
        table[f"{name}_475"] = [level_at_rate(levels, results[name][k], target) for k in range(len(sites))]
    out = Path(ROOT / "validation" / "event_set_check")
    out.mkdir(exist_ok=True)
    table.to_csv(out / "pga475_by_site.csv", index=False)
    np.savez(out / "rates.npz", levels=levels, classical=classical, **results)
    valid = table.dropna()
    summary = {"events": int(len(events)), "years": years, "sites": int(len(sites)), "sites_compared": int(len(valid)),
               "classical_openquake_calculation": args.classical}
    for name in results:
        ratio = valid[f"{name}_475"] / valid.classical_475
        significant = valid.classical_475 > 0.02
        summary[name] = {"median_ratio_475yr_pga": float(ratio.median()),
                         "median_ratio_sites_above_0.02g": float(ratio[significant].median()),
                         "p10_p90_ratio_sites_above_0.02g": [float(ratio[significant].quantile(.1)),
                                                              float(ratio[significant].quantile(.9))]}
        for level in (0.02, 0.05, 0.1):
            i = int(np.argmin(np.abs(levels - level)))
            ratio_of_rates = np.nansum(results[name][:, i]) / np.sum(classical[:, i])
            summary[name][f"rate_ratio_at_pga_{levels[i]:.3f}g"] = float(ratio_of_rates)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
