"""Disaggregate the GEM exposure of Germany (state level) to the Zensus 2022 1 km grid.

Residential buildings: the Zensus counts per cell (by construction decade and building type) give the spatial pattern;
they are scaled to the GEM totals per state. GEM building classes (taxonomies) are assigned to the cells by iterative
proportional fitting (raking), so that the national class mix per settlement type reproduces GEM exactly.
Commercial buildings: the GEM state totals are spread with a population-based proxy. Industrial buildings: the GEM state
totals are spread by the industrial land of CORINE Land Cover (CLC5 2018) in each cell, plus a small population baseline.

Outputs in data/processed:
  cells.parquet         one row per 1 km cell with the exposure per occupancy
  cell_taxonomy.npz     sparse matrix (cells x residential classes) with building counts
  taxonomies.json       class names, their attributes and average building costs
"""
import json
import re

import geopandas as gpd
import numpy as np
import pandas as pd
import scipy.sparse as sp
import shapely
from pyproj import Transformer

from .paths import CLC_INDUSTRIAL, GEM_RAW, PROCESSED, STATES_GEOJSON, ZENSUS_RAW

NUTS1_TO_ISO = {"DE1": "DE-BW", "DE2": "DE-BY", "DE3": "DE-BE", "DE4": "DE-BB", "DE5": "DE-HB", "DE6": "DE-HH",
                "DE7": "DE-HE", "DE8": "DE-MV", "DE9": "DE-NI", "DEA": "DE-NW", "DEB": "DE-RP", "DEC": "DE-SL",
                "DED": "DE-SN", "DEE": "DE-ST", "DEF": "DE-SH", "DEG": "DE-TH"}

AGE_COLS = ["Vor1919", "a1919bis1949", "a1950bis1959", "a1960bis1969", "a1970bis1979", "a1980bis1989",
            "a1990bis1999", "a2000bis2009", "a2010bis2015", "a2016undspaeter"]
TYPE_COLS = ["FreiEFH", "EFH_DHH", "EFH_Reihenhaus", "Freist_ZFH", "ZFH_DHH", "ZFH_Reihenhaus",
             "MFH_3bis6Wohnungen", "MFH_7bis12Wohnungen", "MFH_13undmehrWohnungen", "AndererGebaeudetyp"]

# Assumptions linking Zensus attributes to GEM taxonomy attributes. They are soft prior weights; the raking step
# reconciles them with the GEM class totals.
# Seismic design level by construction decade (first German seismic code DIN 4149 in 1957, revised 1981 and 2005).
CODE_W = np.array([  # rows: AGE_COLS, columns: N(one), L(ow), M(oderate), H(igh) design level
    [1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0], [0.9, 0.1, 0.0, 0.0], [0.6, 0.4, 0.0, 0.0],
    [0.35, 0.6, 0.05, 0.0], [0.2, 0.6, 0.2, 0.0], [0.05, 0.4, 0.5, 0.05], [0.0, 0.1, 0.7, 0.2],
    [0.0, 0.0, 0.5, 0.5], [0.0, 0.0, 0.3, 0.7]]) + 0.02
MATERIALS = ["MUR", "W", "CR", "S", "HYB"]  # masonry, timber, reinforced concrete, steel, hybrid
MAT_W = np.array([  # rows: AGE_COLS, columns: MATERIALS
    [1.5, 0.6, 0.3, 0.3, 1.0], [1.5, 0.6, 0.4, 0.4, 1.0], [1.3, 0.8, 0.8, 0.8, 1.0], [1.0, 1.0, 1.2, 1.2, 1.0],
    [0.8, 1.0, 1.4, 1.4, 1.0], [0.6, 1.2, 1.4, 1.4, 1.0], [0.5, 1.3, 1.3, 1.3, 1.0], [0.4, 1.4, 1.2, 1.2, 1.0],
    [0.3, 1.4, 1.2, 1.2, 1.0], [0.3, 1.4, 1.2, 1.2, 1.0]])
# Height classes (1, 2, 3-5, >5 storeys) as a function of the Zensus building type.
HEIGHT_W = np.array([  # rows: TYPE_COLS
    [0.45, 0.55, 0.0, 0.0], [0.30, 0.65, 0.05, 0.0], [0.10, 0.70, 0.20, 0.0], [0.20, 0.75, 0.05, 0.0],
    [0.15, 0.75, 0.10, 0.0], [0.10, 0.70, 0.20, 0.0], [0.0, 0.35, 0.65, 0.0], [0.0, 0.05, 0.85, 0.10],
    [0.0, 0.0, 0.50, 0.50], [0.30, 0.40, 0.30, 0.0]])
HEIGHT_CLASSES = {"1": 0, "2": 1, "3-5": 2, ">5": 3, "5-8": 3}
HEIGHT_MISMATCH = 0.05  # prior weight for a class whose height differs from the cell's building type
# Relative commercial building density by settlement type (assumption, to be replaced by land-use data).
COM_FACTOR = {"BIG_CITY": 2.0, "URBAN": 1.0, "RURAL": 0.6}
# Share of the industrial buildings of a state that is spread by population instead of by industrial land: small sites
# below the 5 ha mapping limit of CORINE (assumption).
INDUSTRIAL_BASELINE = 0.10
SETTLEMENTS = ["BIG_CITY", "URBAN", "RURAL"]


# ------------------------------------------------------------------------------------------------ input
def _read_zensus(name):
    df = pd.read_csv(ZENSUS_RAW / name, sep=";", na_values="–", encoding="utf-8")  # "–" marks suppressed values
    return df.rename(columns={"GITTER_ID_1km": "id", "x_mp_1km": "x", "y_mp_1km": "y"})


def load_cells():
    """One row per 1 km cell with population and Zensus building counts (union of the population and building grids)."""
    pop = _read_zensus("population_1km.csv")
    age = _read_zensus("buildings_age_1km.csv")
    typ = _read_zensus("buildings_type_1km.csv")
    assert (age["id"].values == typ["id"].values).all()
    cells = pd.merge(pop[["id", "x", "y", "Einwohner"]], age[["id", "x", "y", "Insgesamt_Gebaeude"] + AGE_COLS],
                     on="id", how="outer", suffixes=("", "_b"))
    cells["x"] = cells["x"].fillna(cells["x_b"])
    cells["y"] = cells["y"].fillna(cells["y_b"])
    cells = cells.drop(columns=["x_b", "y_b"]).merge(typ[["id"] + TYPE_COLS], on="id", how="left")
    return cells.rename(columns={"Einwohner": "pop", "Insgesamt_Gebaeude": "bldg_zensus"})


def assign_states(cells):
    """Add lon/lat and the federal state (ISO code) of each cell centre."""
    cells["lon"], cells["lat"] = Transformer.from_crs(3035, 4326, always_xy=True).transform(
        cells["x"].to_numpy(), cells["y"].to_numpy())
    states = gpd.read_file(STATES_GEOJSON)[["NUTS_ID", "geometry"]]
    states["iso"] = states["NUTS_ID"].map(NUTS1_TO_ISO)
    states = states[["iso", "geometry"]].to_crs(4326)
    pts = gpd.GeoDataFrame(cells[["id"]], geometry=gpd.points_from_xy(cells["lon"], cells["lat"]), crs=4326)
    joined = gpd.sjoin(pts, states, how="left", predicate="within").drop_duplicates("id")
    cells["state"] = joined.set_index("id").loc[cells["id"], "iso"].to_numpy()
    outside = cells["state"].isna()
    if outside.any():  # coastal and border cells outside the generalised polygons
        nearest = gpd.sjoin_nearest(pts[outside.to_numpy()].to_crs(3035), states.to_crs(3035),
                                    how="left").drop_duplicates("id")
        cells.loc[outside, "state"] = nearest.set_index("id").loc[cells.loc[outside, "id"], "iso"].to_numpy()


# ------------------------------------------------------------------------------------------------ gap filling
def _fill_suppressed(df, cols, block, state):
    """Replace suppressed category counts: the residual (total - known) is spread with the mix of the known counts
    in the surrounding 10 km block (fallback: the state)."""
    known = df[cols].fillna(0.0).to_numpy()
    resid = np.maximum(df["bldg_zensus"].to_numpy() - known.sum(1), 0.0)
    mix = _local_mix(known, block, state)
    return known + resid[:, None] * mix


def _local_mix(values, block, state):
    """Normalised category mix of the 10 km block of each row (state mix where the block has no information)."""
    block_sum = pd.DataFrame(values).groupby(block).transform("sum").to_numpy()
    state_sum = pd.DataFrame(values).groupby(state).transform("sum").to_numpy()
    mix = np.where(block_sum.sum(1, keepdims=True) > 0, block_sum, state_sum)
    return mix / np.maximum(mix.sum(1, keepdims=True), 1e-12)


def fill_gaps(cells):
    """Complete population and buildings per cell and return the age and type shares of the buildings in each cell."""
    both = cells["pop"].notna() & cells["bldg_zensus"].notna()
    ratio = cells[both].groupby("state")["pop"].sum() / cells[both].groupby("state")["bldg_zensus"].sum()
    ratio = cells["state"].map(ratio)
    cells["pop_f"] = cells["pop"].fillna(cells["bldg_zensus"] * ratio)
    cells["bldg_f"] = cells["bldg_zensus"].fillna(cells["pop"] / ratio)

    block = ((cells["x"] // 10000).astype(int).astype(str) + "_" + (cells["y"] // 10000).astype(int).astype(str))
    block, state = block.to_numpy(), cells["state"].to_numpy()
    has = cells["bldg_zensus"].notna().to_numpy()
    shares = []
    for cols in (AGE_COLS, TYPE_COLS):
        counts = np.zeros((len(cells), len(cols)))
        counts[has] = _fill_suppressed(cells[has], cols, block[has], state[has])
        counts[~has] = _local_mix(counts, block, state)[~has]  # cells without a building record: block / state mix
        shares.append(counts / np.maximum(counts.sum(1, keepdims=True), 1e-12))
    return shares


# ------------------------------------------------------------------------------------------------ taxonomy
def classify_settlements(cells, tax):
    """Settlement type per cell from the population density, calibrated to the GEM national shares of buildings."""
    shares = tax[tax["OCCUPANCY"] == "RES"].groupby("SETTLEMENT")["BUILDINGS"].sum()
    shares = shares[SETTLEMENTS] / shares.sum()
    order = np.argsort(-cells["pop_f"].to_numpy(), kind="stable")
    cum = np.cumsum(cells["res_bldg"].to_numpy()[order]) / cells["res_bldg"].sum()
    settlement = np.empty(len(cells), dtype=object)
    settlement[order] = np.where(cum <= shares["BIG_CITY"], "BIG_CITY",
                                 np.where(cum <= shares["BIG_CITY"] + shares["URBAN"], "URBAN", "RURAL"))
    cells["settlement"] = settlement


def _taxonomy_attributes(name):
    """(material index, design-level index, height class) of a GEM taxonomy string."""
    material = name.split("/")[0].split("+")[0].split("(")[0]
    design = re.search(r"CD([NLMH])", name).group(1)
    height = HEIGHT_CLASSES[name.split("H:")[1].split("/")[0]]
    return MATERIALS.index(material if material in MATERIALS else "HYB"), "NLMH".index(design), height


def assign_taxonomy(cells, res_tax, age_share, type_share):
    """Building counts per cell and GEM class (sparse), by raking per settlement type.

    A group is (construction decade, height class). For each settlement type the matrix group x class is fitted
    (IPF) so that its row sums equal the Zensus group counts and its column sums the GEM class counts. The prior
    follows the assumptions in CODE_W, MAT_W and HEIGHT_MISMATCH.
    """
    taxa = sorted(res_tax["TAXONOMY"].unique())
    attrs = np.array([_taxonomy_attributes(t) for t in taxa])
    gem_counts = res_tax.pivot_table(index="TAXONOMY", columns="SETTLEMENT", values="BUILDINGS",
                                     aggfunc="sum").reindex(taxa).fillna(0.0)
    n_age, n_height = len(AGE_COLS), HEIGHT_W.shape[1]
    height_share = type_share @ HEIGHT_W
    height_share = height_share / np.maximum(height_share.sum(1, keepdims=True), 1e-12)

    conditional = {}  # settlement -> P(class | group), shape (groups, classes)
    for s in SETTLEMENTS:
        mask = (cells["settlement"] == s).to_numpy()
        weights = cells.loc[mask, "res_bldg"].to_numpy()
        group_counts = np.einsum("c,ca,ch->ah", weights, age_share[mask], height_share[mask]).reshape(-1)
        target = gem_counts[s].to_numpy()
        group_counts = group_counts * target.sum() / group_counts.sum()
        fit = np.zeros((n_age * n_height, len(taxa)))
        for a in range(n_age):
            for h in range(n_height):
                prior = CODE_W[a, attrs[:, 1]] * MAT_W[a, attrs[:, 0]] * np.where(attrs[:, 2] == h, 1.0, HEIGHT_MISMATCH)
                fit[a * n_height + h] = target * prior
        fit += 1e-9 * target
        for _ in range(200):
            fit *= (group_counts / np.maximum(fit.sum(1), 1e-12))[:, None]
            fit *= (target / np.maximum(fit.sum(0), 1e-12))[None, :]
        residual = np.abs(fit.sum(1) - group_counts).sum() / group_counts.sum()
        print(f"  {s}: IPF residual on group totals {residual:.3%}")
        conditional[s] = fit / np.maximum(fit.sum(1, keepdims=True), 1e-12)

    rows, cols, vals = [], [], []
    for s in SETTLEMENTS:
        idx = np.where((cells["settlement"] == s).to_numpy())[0]
        group_share = (age_share[idx, :, None] * height_share[idx, None, :]).reshape(len(idx), -1)
        prob = group_share @ conditional[s]
        prob = prob / np.maximum(prob.sum(1, keepdims=True), 1e-12)
        counts = prob * cells["res_bldg"].to_numpy()[idx, None]
        counts[counts < 1e-4] = 0
        r, c = np.nonzero(counts)
        rows.append(idx[r])
        cols.append(c)
        vals.append(counts[r, c])
    matrix = sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                           shape=(len(cells), len(taxa)))
    return matrix, taxa, attrs, gem_counts


# ------------------------------------------------------------------------------------------------ costs
def add_costs(cells, matrix, taxa, res_tax, gem_res):
    """Residential cost, contents and occupants per cell, scaled to the GEM totals of each state."""
    cost_per_building = (res_tax["BLDG_REPL_COST_USD"] / res_tax["BUILDINGS"]).rename("cost")
    avg_cost = res_tax.assign(cost=cost_per_building).pivot_table(
        index="TAXONOMY", columns="SETTLEMENT", values="cost", aggfunc="mean").reindex(taxa)
    avg_cost = avg_cost.fillna(avg_cost.mean())
    cost = np.zeros(len(cells))
    for s in SETTLEMENTS:
        idx = np.where((cells["settlement"] == s).to_numpy())[0]
        cost[idx] = matrix[idx] @ avg_cost[s].to_numpy()
    cells["res_bldg_cost"] = cost
    cells["res_bldg_cost"] *= cells["state"].map(
        gem_res["BLDG_REPL_COST_USD"] / cells.groupby("state")["res_bldg_cost"].sum())
    cells["res_contents_cost"] = cells["res_bldg_cost"] * cells["state"].map(
        gem_res["COST_CONTENTS_USD"] / gem_res["BLDG_REPL_COST_USD"])
    cells["res_occupants"] = cells["pop_f"] * cells["state"].map(
        gem_res["OCCUPANTS_TOTAL"] / cells.groupby("state")["pop_f"].sum())
    cells["res_area_sqm"] = cells["res_bldg"] * cells["state"].map(gem_res["AVG_BUILDING_AREA_SQM"])
    return avg_cost


def industrial_land_area(cells):
    """Area in m2 of industrial land inside each 1 km cell: CORINE CLC5 2018 classes 121 (industrial or commercial
    units), 123 (port areas) and 124 (airports)."""
    polygons = gpd.read_parquet(CLC_INDUSTRIAL).to_crs(3035)
    geometry = shapely.make_valid(polygons.geometry.to_numpy())
    x, y = cells["x"].to_numpy(), cells["y"].to_numpy()  # cell centres in EPSG:3035
    squares = shapely.box(x - 500, y - 500, x + 500, y + 500)
    polygon_index, cell_index = shapely.STRtree(squares).query(geometry, predicate="intersects")
    overlap = shapely.area(shapely.intersection(geometry[polygon_index], squares[cell_index]))
    return np.bincount(cell_index, weights=overlap, minlength=len(cells))


def add_commercial_industrial(cells, adm1):
    """Spread the GEM state totals of commercial and industrial buildings over the cells of each state."""
    cells["ind_land_m2"] = industrial_land_area(cells)
    population = cells["pop_f"] * cells["settlement"].map(COM_FACTOR)
    by_state = cells["state"]
    for occupancy, key in (("COM", "com"), ("IND", "ind")):
        gem = adm1[adm1["OCCUPANCY"] == occupancy].set_index("ID_1")
        if occupancy == "COM":
            share = population / population.groupby(by_state).transform("sum")
        else:  # industrial land (CORINE) plus a population baseline for small sites
            plain = cells["pop_f"] / cells["pop_f"].groupby(by_state).transform("sum")
            land = cells["ind_land_m2"] / cells["ind_land_m2"].groupby(by_state).transform("sum")
            share = INDUSTRIAL_BASELINE * plain + (1 - INDUSTRIAL_BASELINE) * land.fillna(plain)
        cells[f"{key}_bldg"] = share * cells["state"].map(gem["BUILDINGS"])
        cells[f"{key}_bldg_cost"] = share * cells["state"].map(gem["BLDG_REPL_COST_USD"])
        cells[f"{key}_contents_cost"] = share * cells["state"].map(gem["COST_CONTENTS_USD"])
        cells[f"{key}_area_sqm"] = share * cells["state"].map(gem["TOTAL_AREA_SQM"])


def report(cells, matrix, gem_counts, adm1):
    """Print how well the disaggregated exposure reproduces the GEM totals."""
    print("Check against the GEM totals")
    for occupancy, key in (("RES", "res"), ("COM", "com"), ("IND", "ind")):
        gem = adm1[adm1["OCCUPANCY"] == occupancy]
        print(f"  {occupancy}: buildings {cells[f'{key}_bldg'].sum() / gem['BUILDINGS'].sum():.4f}, "
              f"cost {cells[f'{key}_bldg_cost'].sum() / gem['BLDG_REPL_COST_USD'].sum():.4f} of GEM")
    print(f"  residential occupants: {cells['res_occupants'].sum() / 1e6:.2f} million")
    print("  cells per settlement type:", cells["settlement"].value_counts().to_dict())
    model = np.asarray(matrix.sum(0)).ravel()
    gem = gem_counts.sum(1).to_numpy()
    large = gem > 0.01 * gem.sum()
    print(f"  max relative class deviation (classes above 1 % of the stock): "
          f"{(np.abs(model - gem) / gem)[large].max():.3f}")


# ------------------------------------------------------------------------------------------------ main
def build():
    PROCESSED.mkdir(parents=True, exist_ok=True)
    adm1 = pd.read_csv(GEM_RAW / "Exposure_Summary_Adm1.csv", encoding="utf-8")
    tax = pd.read_csv(GEM_RAW / "Exposure_Summary_Taxonomy.csv", encoding="utf-8")
    res_tax = tax[tax["OCCUPANCY"] == "RES"].copy()
    gem_res = adm1[adm1["OCCUPANCY"] == "RES"].set_index("ID_1")

    print("Zensus grid")
    cells = load_cells()
    assign_states(cells)
    age_share, type_share = fill_gaps(cells)
    print(f"  {len(cells)} cells")

    # scale the residential buildings to the GEM totals of each state
    cells["res_bldg"] = cells["bldg_f"]
    factor = gem_res["BUILDINGS"] / cells.groupby("state")["res_bldg"].sum()
    cells["res_bldg"] *= cells["state"].map(factor)
    print("  scale factors GEM / Zensus per state:", factor.round(3).to_dict())

    print("Building classes")
    classify_settlements(cells, tax)
    matrix, taxa, attrs, gem_counts = assign_taxonomy(cells, res_tax, age_share, type_share)
    avg_cost = add_costs(cells, matrix, taxa, res_tax, gem_res)
    add_commercial_industrial(cells, adm1)

    keep = ["id", "x", "y", "lon", "lat", "state", "settlement", "pop", "pop_f", "res_bldg", "res_bldg_cost",
            "res_contents_cost", "res_occupants", "res_area_sqm", "com_bldg", "com_bldg_cost", "com_contents_cost",
            "com_area_sqm", "ind_bldg", "ind_bldg_cost", "ind_contents_cost", "ind_area_sqm", "ind_land_m2"]
    cells[keep].to_parquet(PROCESSED / "cells.parquet", index=False)
    sp.save_npz(PROCESSED / "cell_taxonomy.npz", matrix)
    (PROCESSED / "taxonomies.json").write_text(json.dumps({
        "taxonomies": taxa,
        "avg_bldg_cost_usd": {s: avg_cost[s].round(0).tolist() for s in SETTLEMENTS},
        "attributes_material_code_height": attrs.tolist(), "materials": MATERIALS}), encoding="utf-8")
    report(cells, matrix, gem_counts, adm1)
