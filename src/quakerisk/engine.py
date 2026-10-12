"""Scenario earthquake loss engine for the disaggregated German exposure.

For a scenario (epicentre, magnitude, depth, fault) the engine computes
  1. the ground motion at every 1 km cell (hazard.py),
  2. the expected loss ratio of every vulnerability function, integrated over the lognormal ground-motion variability
     (quadrature on the normal distribution truncated at 3 sigma; exact for expected values, spatial correlation of the
     variability only matters for the spread of the total),
  3. structural, non-structural and contents losses, fatalities and damage grades per cell, and their aggregates.
A scenario takes about one second. engine_data.prepare() builds the exposure matrices once.
"""
import numpy as np
import pandas as pd
from scipy.special import betainc

from . import engine_data
from .hazard import IMTS, VS30_DEFAULT, GroundMotionModel, mmi_from_pga
from .vulnerability import load_vulnerability

OCCUPANCIES = ("res", "com", "ind")
LOSS_CATEGORIES = ("structural", "nonstructural", "contents", "fatalities")
# Share of the building replacement cost that is structural (the rest is non-structural). The GEM summaries do not
# give this split; the values are assumptions of HAZUS-like magnitude.
STRUCTURAL_SHARE = {"res": 0.30, "com": 0.25, "ind": 0.25}
# Structural loss-ratio bands from which the damage grades are derived (the public data has no fragility curves).
GRADE_THRESHOLDS = [0.02, 0.10, 0.30, 0.70]
GRADE_NAMES = ["slight", "moderate", "extensive", "complete"]
MIN_PGA_G = 0.03  # below this (median plus three sigma) the vulnerability curves give no loss
TRUNCATION = 3.0  # sigma
LN_MEDIAN_GRID = np.linspace(np.log(0.002), np.log(6.0), 260)  # lookup grid of ln(median intensity in g)


class QuakeEngine:
    def __init__(self):
        data = engine_data.load()
        self.cells = data["cells"]
        self.functions = data["functions"]
        self.matrices = data["matrices"]
        self.mix = data["mix"]
        self.hexagons = data["hex"]
        curves = load_vulnerability()
        self.curves = {category: [curves[category][f] for f in self.functions] for category in curves}
        self.function_imt = [IMTS.index(c["imt"]) for c in self.curves["structural"]]
        self.ground_motion = GroundMotionModel(self.cells.lon.to_numpy(), self.cells.lat.to_numpy(),
                                               self.cells.x.to_numpy(), self.cells.y.to_numpy())
        # Gauss-Legendre nodes and weights for the standard normal truncated at +-3 sigma
        nodes, weights = np.polynomial.legendre.leggauss(21)
        self.z = TRUNCATION * nodes
        density = np.exp(-0.5 * self.z ** 2) / np.sqrt(2 * np.pi)
        self.weights = TRUNCATION * weights * density
        self.weights /= self.weights.sum()
        self._tables = {}

    # ------------------------------------------------------------------------------------ vulnerability
    @staticmethod
    def _loss_ratio(curve, intensity):
        """Mean loss ratio at the given intensity (g); zero below the first intensity level of the curve."""
        ratio = np.interp(np.log(intensity), np.log(curve["imls"]), curve["mean"])
        return np.where(intensity < curve["imls"][0], 0.0, ratio)

    @staticmethod
    def _exceedance(curve, intensity, threshold):
        """P(loss ratio > threshold) for the beta-distributed loss ratio at the given intensity."""
        log_i = np.log(intensity)
        mean = np.clip(np.interp(log_i, np.log(curve["imls"]), curve["mean"]), 1e-9, 1 - 1e-9)
        cov = np.interp(log_i, np.log(curve["imls"]), curve["cov"])
        variance = np.maximum((cov * mean) ** 2, 1e-12)
        k = np.maximum(mean * (1 - mean) / variance - 1, 1e-3)
        return np.where(intensity < curve["imls"][0], 0.0, 1 - betainc(mean * k, (1 - mean) * k, threshold))

    def _table(self, imt_index, sigma):
        """Expected loss ratios and exceedance probabilities on the ln-median grid, for all functions of one IMT."""
        key = (imt_index, round(float(sigma), 2))
        if key not in self._tables:
            members = [f for f in range(len(self.functions)) if self.function_imt[f] == imt_index]
            intensity = np.exp(LN_MEDIAN_GRID[:, None] + key[1] * self.z[None, :])  # (grid, quadrature nodes)
            table = np.zeros((len(members), len(LOSS_CATEGORIES) + len(GRADE_THRESHOLDS), len(LN_MEDIAN_GRID)))
            for a, f in enumerate(members):
                for k, category in enumerate(LOSS_CATEGORIES):
                    table[a, k] = self._loss_ratio(self.curves[category][f], intensity) @ self.weights
                for k, threshold in enumerate(GRADE_THRESHOLDS):
                    table[a, len(LOSS_CATEGORIES) + k] = (
                        self._exceedance(self.curves["structural"][f], intensity, threshold) @ self.weights)
            self._tables[key] = (members, table)
        return self._tables[key]

    def expected(self, mean, sigma, chunk=20000):
        """Expected loss ratios per cell x function ({category: array}) and the expected exceedance probability of
        every structural loss-ratio band (array (bands, cells, functions))."""
        n_cells, n_functions = mean.shape[1], len(self.functions)
        out = np.zeros((len(LOSS_CATEGORIES) + len(GRADE_THRESHOLDS), n_cells, n_functions))
        step = LN_MEDIAN_GRID[1] - LN_MEDIAN_GRID[0]
        for j in range(len(IMTS)):
            if n_cells == 0:  # no cell is shaken enough to cause loss
                break
            members, table = self._table(j, float(np.mean(sigma[j])))
            if not members:
                continue
            for start in range(0, n_cells, chunk):
                position = np.clip((mean[j, start:start + chunk] - LN_MEDIAN_GRID[0]) / step, 0, len(LN_MEDIAN_GRID) - 1.001)
                lower = position.astype(int)
                frac = position - lower
                values = table[:, :, lower] * (1 - frac) + table[:, :, lower + 1] * frac  # (members, outputs, cells)
                out[:, start:start + chunk][:, :, members] = np.moveaxis(values, 0, 2)
        n = len(LOSS_CATEGORIES)
        return dict(zip(LOSS_CATEGORIES, out[:n])), out[n:]

    # ------------------------------------------------------------------------------------ scenario
    def run(self, lon, lat, mag, depth=10.0, strike=0.0, dip=60.0, rake=-90.0, vs30=VS30_DEFAULT, gmpe="ensemble"):
        near, rjb, mean, sigma, surface = self.ground_motion.evaluate(lon, lat, mag, depth, strike, dip, rake, vs30, gmpe)
        keep = np.exp(mean[0]) * np.exp(3 * sigma[0]) > MIN_PGA_G
        cells = near[keep]
        mean, sigma = mean[:, keep], sigma[:, keep]
        loss_ratio, exceedance = self.expected(mean, sigma)

        n = len(cells)
        building_value = self.matrices["building_value"][cells].tocoo()
        contents_value = self.matrices["contents_value"][cells].tocoo()
        buildings = self.matrices["buildings"][cells].tocoo()
        residents = self.matrices["residents"][cells].tocoo()

        def weighted(matrix, per_function):  # sum over functions of matrix[cell, function] * per_function[cell, function]
            return np.bincount(matrix.row, matrix.data * per_function[matrix.row, matrix.col], minlength=n)

        def bands(counts):  # exceedance counts -> exclusive damage bands (slight ... complete)
            return np.concatenate([counts[:, :-1] - counts[:, 1:], counts[:, -1:]], axis=1)

        share = STRUCTURAL_SHARE["res"]
        results = {"res": {
            "structural": weighted(building_value, loss_ratio["structural"]) * share,
            "nonstructural": weighted(building_value, loss_ratio["nonstructural"]) * (1 - share),
            "contents": weighted(contents_value, loss_ratio["contents"]),
            "fatalities": weighted(residents, loss_ratio["fatalities"]),
            "grades": bands(np.stack([weighted(buildings, exceedance[k]) for k in range(len(GRADE_THRESHOLDS))], 1))}}
        value = {"res": np.bincount(building_value.row, building_value.data, minlength=n)
                 + np.bincount(contents_value.row, contents_value.data, minlength=n)}
        for occupancy in ("com", "ind"):  # one national class mix: [count share, value share] per function
            count_mix, value_mix = self.mix[occupancy]
            building_cost = self.cells[f"{occupancy}_bldg_cost"].to_numpy()[cells]
            contents_cost = self.cells[f"{occupancy}_contents_cost"].to_numpy()[cells]
            n_buildings = self.cells[f"{occupancy}_bldg"].to_numpy()[cells]
            share = STRUCTURAL_SHARE[occupancy]
            results[occupancy] = {
                "structural": building_cost * share * (loss_ratio["structural"] @ value_mix),
                "nonstructural": building_cost * (1 - share) * (loss_ratio["nonstructural"] @ value_mix),
                "contents": contents_cost * (loss_ratio["contents"] @ value_mix),
                "fatalities": np.zeros(n),  # GEM has no occupants for commercial and industrial buildings
                "grades": bands(np.stack([n_buildings * (exceedance[k] @ count_mix)
                                          for k in range(len(GRADE_THRESHOLDS))], 1))}
            value[occupancy] = building_cost + contents_cost
        scenario = {"lon": lon, "lat": lat, "mag": mag, "depth": depth, "strike": strike, "dip": dip, "rake": rake,
                    "vs30": vs30, "gmpe": gmpe,
                    "rupture": [[float(a), float(b)] for a, b in zip(surface.corner_lons, surface.corner_lats)]}
        return Result(self, cells, rjb[keep], np.exp(mean), sigma, results, value, scenario, surface)


class Result:
    """Outcome of one scenario: per-cell results for the shaken cells and their aggregates."""

    def __init__(self, engine, cells, rjb, ground_motion, sigma, results, value, scenario, surface):
        self.engine, self.cells, self.rjb = engine, cells, rjb
        self.ground_motion, self.sigma = ground_motion, sigma  # (imts, cells) in g
        self.results, self.value = results, value  # per occupancy / exposed value per occupancy
        self.scenario, self.surface = scenario, surface

    def cell_table(self):
        c = self.engine.cells.iloc[self.cells]
        table = {"id": c["id"].to_numpy(), "lon": c.lon.to_numpy(), "lat": c.lat.to_numpy(), "state": c.state.to_numpy(),
                 "rjb_km": self.rjb, "pga_g": self.ground_motion[0], "sa03_g": self.ground_motion[1],
                 "sa10_g": self.ground_motion[3], "mmi": mmi_from_pga(self.ground_motion[0])}
        loss = 0
        for occupancy in OCCUPANCIES:
            r = self.results[occupancy]
            table[f"loss_{occupancy}"] = r["structural"] + r["nonstructural"] + r["contents"]
            table[f"value_{occupancy}"] = self.value[occupancy]
            loss = loss + table[f"loss_{occupancy}"]
            for k, grade in enumerate(GRADE_NAMES):
                table[f"{grade}_{occupancy}"] = r["grades"][:, k]
        table["loss"] = loss
        table["fatalities"] = self.results["res"]["fatalities"]
        table["value"] = sum(table[f"value_{o}"] for o in OCCUPANCIES)
        return pd.DataFrame(table)

    def hex_layers(self, resolution):
        """Cell results aggregated to H3 hexagons (resolution 6 or 7), as compact lists for the map."""
        table = self.cell_table()
        table["hex"] = self.engine.hexagons[resolution][self.cells]
        grouped = table.groupby("hex").agg(pga=("pga_g", "mean"), loss=("loss", "sum"), fat=("fatalities", "sum"),
                                           value=("value", "sum"))
        grouped["mmi"] = mmi_from_pga(grouped.pga.to_numpy())
        grouped = grouped[grouped.mmi >= 3.0]
        return {"ids": grouped.index.tolist(), "mmi": grouped.mmi.round(2).tolist(), "pga": grouped.pga.round(4).tolist(),
                "loss": (grouped.loss / 1e6).round(3).tolist(), "fat": grouped.fat.round(3).tolist(),
                "value": (grouped.value / 1e6).round(1).tolist()}

    def totals(self):
        """Scalar totals of the scenario (cheap; used for event loss tables of many events)."""
        loss = {o: float(sum(self.results[o][k].sum() for k in ("structural", "nonstructural", "contents")))
                for o in OCCUPANCIES}
        return {"loss": sum(loss.values()), **{f"loss_{o}": v for o, v in loss.items()},
                "fatalities": float(self.results["res"]["fatalities"].sum()), "n_cells": int(len(self.cells)),
                "max_pga_g": float(self.ground_motion[0].max()) if len(self.cells) else 0.0}

    def summary(self):
        table = self.cell_table()

        def total(key):
            return float(sum(self.results[o][key].sum() for o in OCCUPANCIES))

        out = {"scenario": self.scenario, "n_cells_affected": int(len(table)),
               "loss_usd": float(table.loss.sum()), "exposed_value_usd": float(table.value.sum()),
               "fatalities": float(table.fatalities.sum()),
               "max_pga_g": float(table.pga_g.max()), "max_mmi": float(table.mmi.max())}
        out["loss_ratio"] = out["loss_usd"] / max(out["exposed_value_usd"], 1)
        out["loss_by_component_usd"] = {k: total(k) for k in ("structural", "nonstructural", "contents")}
        out["loss_by_occupancy_usd"] = {o: float(table[f"loss_{o}"].sum()) for o in OCCUPANCIES}
        out["buildings_by_grade"] = {g: float(sum(self.results[o]["grades"][:, k].sum() for o in OCCUPANCIES))
                                     for k, g in enumerate(GRADE_NAMES)}
        out["by_state"] = table.groupby("state")[["loss", "fatalities"]].sum().round(0).reset_index().to_dict("records")
        residents = self.engine.cells.res_occupants.to_numpy()[self.cells]
        out["people_exposed_mmi"] = {f"MMI>={m}": float(residents[table.mmi.to_numpy() >= m].sum()) for m in (5, 6, 7)}
        return out
