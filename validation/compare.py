"""Compare the fast engine with the OpenQuake run made by run_validation.py.

Usage:  python validation/compare.py [name]        (default: duren_m58)
Writes validation/<name>/comparison.json and comparison.png.
"""
import json
import sys

import numpy as np
import pandas as pd

from quakerisk.paths import PROCESSED, ROOT

ECONOMIC = ["structural", "nonstructural", "contents"]
IMTS = ["PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)"]
DISTANCE_BANDS = [0, 10, 20, 40, 70, 120, 400]  # Joyner-Boore distance in km
OPENQUAKE_LOSS_TYPES = {"structural": "structural", "nonstructural": "nonstructural", "contents": "contents",
                        "occupants": "fatalities"}  # OpenQuake loss type -> key in the fast engine results


def compare_ground_motion(ds, ours, events):
    """Median ground motion of the sampled OpenQuake fields against the median of the fast engine."""
    gmf = ds.read_df("gmf_data")
    sites = ds["sitecol"].array
    cells = pd.read_parquet(PROCESSED / "cells.parquet", columns=["lon", "lat"]).iloc[ours["sel"]]

    def key(lon, lat):
        return np.round(lon, 5) * 1e6 + np.round(lat, 5)

    cell_of_key = pd.Series(np.arange(len(cells)), index=key(cells.lon.to_numpy(), cells.lat.to_numpy()))
    site_cell = pd.Series(cell_of_key.reindex(key(sites["lon"], sites["lat"])).to_numpy(), index=sites["sids"])
    rows = []
    for imt in list(ds["oqparam"].imtls):
        log_gm = np.log(gmf[imt].clip(lower=1e-6))
        stats = log_gm.groupby(gmf["sid"].astype(int)).agg(["mean", "std", "count"]).dropna()
        stats["cell"] = site_cell.reindex(stats.index).to_numpy()
        # OpenQuake only stores values above a threshold, so use the sites where all events are stored
        stats = stats[(stats["count"] == events) & stats["cell"].notna()]
        k = IMTS.index(imt)
        cell = stats["cell"].astype(int).to_numpy()
        difference = stats["mean"].to_numpy() - np.log(ours["gm"][k, cell])
        rows.append({"imt": imt, "sites": len(stats), "mean_ln_diff": float(difference.mean()),
                     "p95_abs_ln_diff": float(np.percentile(np.abs(difference), 95)),
                     "openquake_sigma": float(stats["std"].mean()), "fast_engine_sigma": float(ours["sig"][k, cell].mean())})
    return rows


def compare_losses(ds, ours, assets, events):
    """Totals per loss type, per-cell losses of both engines and the Monte-Carlo error of the OpenQuake mean."""
    loss_types = list(ds["oqparam"].loss_types)
    ids = np.array([a.decode() if isinstance(a, bytes) else a for a in ds["assetcol"].array["id"]])
    average = pd.DataFrame({lt: ds["avg_losses-rlzs"][lt][:, 0] for lt in loss_types}, index=ids).reindex(assets["id"])
    per_cell = average.groupby(assets["cell"].to_numpy()).sum()

    totals, cells = {}, pd.DataFrame(index=np.arange(len(ours["sel"])))
    for loss_type, key in OPENQUAKE_LOSS_TYPES.items():
        fast = sum(ours[f"{o}_{key}"] for o in ("res", "com", "ind"))
        totals[loss_type] = {"fast_engine": float(fast.sum()), "openquake": float(per_cell[loss_type].sum())}
        totals[loss_type]["ratio"] = totals[loss_type]["fast_engine"] / max(totals[loss_type]["openquake"], 1e-9)
        cells[f"{loss_type}_ours"] = fast
        cells[f"{loss_type}_oq"] = per_cell[loss_type].reindex(cells.index).fillna(0).to_numpy()
    economic = {s: sum(totals[t][s] for t in ECONOMIC) for s in ("fast_engine", "openquake")}
    economic["ratio"] = economic["fast_engine"] / economic["openquake"]

    by_event = ds.read_df("risk_by_event").groupby(["event_id", "loss_id"])["loss"].sum().unstack().fillna(0.0)
    occupants_id = by_event.sum().idxmin()  # loss ids are internal; the occupants column is the tiny fatality count
    event_loss = by_event.drop(columns=[occupants_id]).sum(1).reindex(range(events)).fillna(0.0)
    standard_error = float(event_loss.std() / np.sqrt(len(event_loss)))
    monte_carlo = {"events": int(len(event_loss)), "mean": float(event_loss.mean()), "std_error": standard_error,
                   "relative_std_error": standard_error / float(event_loss.mean()),
                   "cv_between_events": float(event_loss.std() / event_loss.mean())}
    economic["within_two_std_errors"] = bool(abs(economic["ratio"] - 1) < 2 * monte_carlo["relative_std_error"])
    totals["economic_total"] = economic
    return totals, monte_carlo, cells


def by_distance(cells, rjb):
    rows = []
    for low, high in zip(DISTANCE_BANDS[:-1], DISTANCE_BANDS[1:]):
        mask = (rjb >= low) & (rjb < high)
        fast = sum(cells[f"{t}_ours"][mask].sum() for t in ECONOMIC)
        openquake = sum(cells[f"{t}_oq"][mask].sum() for t in ECONOMIC)
        rows.append({"rjb_km": f"{low}-{high}", "cells": int(mask.sum()), "fast_engine_usd": float(fast),
                     "openquake_usd": float(openquake), "ratio": float(fast / openquake) if openquake > 0 else None})
    return rows


def plot(folder, cells, bands, ground_motion):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))
    fast = sum(cells[f"{t}_ours"] for t in ECONOMIC) / 1e6
    openquake = sum(cells[f"{t}_oq"] for t in ECONOMIC) / 1e6
    mask = (fast > 1e-3) | (openquake > 1e-3)
    axes[0].loglog(openquake[mask].clip(lower=1e-3), fast[mask].clip(lower=1e-3), ".", ms=2, alpha=0.35)
    limit = [1e-3, max(fast.max(), openquake.max()) * 1.2]
    axes[0].plot(limit, limit, "k-", lw=0.8)
    axes[0].set(xlabel="OpenQuake loss per 1 km cell (MUSD)", ylabel="fast engine (MUSD)", title="Economic loss per cell")
    table = pd.DataFrame(bands)
    axes[1].bar(table["rjb_km"], table["openquake_usd"] / 1e6, width=0.4, align="edge", label="OpenQuake")
    axes[1].bar(table["rjb_km"], table["fast_engine_usd"] / 1e6, width=-0.4, align="edge", label="fast engine")
    axes[1].set(xlabel="Joyner-Boore distance (km)", ylabel="loss (MUSD)", title="Loss by distance band")
    axes[1].legend()
    gm = pd.DataFrame(ground_motion)
    axes[2].bar(gm["imt"], gm["mean_ln_diff"])
    axes[2].axhline(0, color="k", lw=0.8)
    axes[2].set(ylabel="mean ln(OpenQuake) - ln(fast engine)", title="Median ground motion difference")
    fig.tight_layout()
    fig.savefig(folder / "comparison.png", dpi=90)


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "duren_m58"
    folder = ROOT / "validation" / name
    meta = json.loads((folder / "meta.json").read_text())
    from openquake.commonlib import datastore

    ds = datastore.read(meta["calc_id"])
    ours = np.load(folder / "ours_components.npz")
    assets = pd.read_parquet(folder / "assets_ours.parquet")

    ground_motion = compare_ground_motion(ds, ours, meta["events"])
    totals, monte_carlo, cells = compare_losses(ds, ours, assets, meta["events"])
    bands = by_distance(cells, ours["rjb"])
    out = {"scenario": meta["scenario"], "events": meta["events"], "ground_motion": ground_motion, "totals": totals,
           "openquake_monte_carlo": monte_carlo, "by_distance": bands}
    (folder / "comparison.json").write_text(json.dumps(out, indent=1))
    plot(folder, cells, bands, ground_motion)

    print("scenario", meta["scenario"], "| events", meta["events"])
    print("\nGround motion (OpenQuake sampled median vs fast engine, ln units)")
    print(pd.DataFrame(ground_motion).round(3).to_string(index=False))
    print("\nTotal losses (USD; occupants = fatalities)")
    print(pd.DataFrame(totals).T.round(3).to_string())
    print("\nOpenQuake Monte-Carlo error:", {k: round(v, 3) for k, v in monte_carlo.items()})
    print("\nBy distance")
    print(pd.DataFrame(bands).round(3).to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
