"""Exposure matrices for the loss engine: cells x vulnerability function.

Residential exposure is stored per cell and function (values, buildings and residents); commercial and industrial
exposure follows one national class mix, so it is kept as a single mix vector per occupancy.
"""
import json

import h3
import numpy as np
import pandas as pd
import scipy.sparse as sp

from .paths import GEM_RAW, PROCESSED

SETTLEMENT_INDEX = {"BIG_CITY": 0, "URBAN": 1, "RURAL": 2}
HEX_RESOLUTIONS = (6, 7)

FILES = {name: PROCESSED / f"engine_{name}.npz" for name in ("building_value", "contents_value", "buildings", "residents")}
MIX_FILE = PROCESSED / "engine_mix.npz"  # commercial / industrial class mix: [count share, value share] per function
HEX_FILE = PROCESSED / "engine_hex.npz"  # H3 cell of every 1 km cell
FUNCTIONS_FILE = PROCESSED / "engine_functions.json"


def _row_normalise(matrix):
    totals = np.asarray(matrix.sum(1)).ravel()
    return sp.diags(1.0 / np.maximum(totals, 1e-9)) @ matrix


def prepare():
    """Build the exposure matrices from the disaggregated exposure and the class-to-function mapping."""
    cells = pd.read_parquet(PROCESSED / "cells.parquet")
    counts = sp.load_npz(PROCESSED / "cell_taxonomy.npz").tocsr()
    taxonomies = json.loads((PROCESSED / "taxonomies.json").read_text(encoding="utf-8"))
    mapping = pd.read_csv(PROCESSED / "taxonomy_vulnerability_map.csv")
    function_of = dict(zip(mapping.taxonomy, mapping.vuln_id))
    taxa = taxonomies["taxonomies"]
    functions = sorted(set(mapping.vuln_id))
    index = {f: i for i, f in enumerate(functions)}
    to_function = sp.csr_matrix((np.ones(len(taxa)), (np.arange(len(taxa)), [index[function_of[t]] for t in taxa])),
                                shape=(len(taxa), len(functions)))

    # residential: value shares per class (average class cost x number), scaled to the modelled value of each cell
    class_cost = np.array([taxonomies["avg_bldg_cost_usd"][s] for s in SETTLEMENT_INDEX])  # (settlements, classes)
    settlement = cells["settlement"].map(SETTLEMENT_INDEX).to_numpy()
    value_share = _row_normalise(counts.multiply(class_cost[settlement]).tocsr())
    matrices = {
        "building_value": sp.diags(cells.res_bldg_cost.to_numpy()) @ value_share @ to_function,
        "contents_value": sp.diags(cells.res_contents_cost.to_numpy()) @ value_share @ to_function,
        "buildings": counts @ to_function,
        "residents": sp.diags(cells.res_occupants.to_numpy()) @ _row_normalise(counts) @ to_function,
    }

    # commercial and industrial: national class mix, identical for every cell
    taxonomy = pd.read_csv(GEM_RAW / "Exposure_Summary_Taxonomy.csv", encoding="utf-8")
    mixes = {}
    for occupancy in ("COM", "IND"):
        by_class = taxonomy[taxonomy.OCCUPANCY == occupancy].groupby("TAXONOMY")[["BUILDINGS", "BLDG_REPL_COST_USD"]].sum()
        mix = np.zeros((2, len(functions)))
        for name, row in by_class.iterrows():
            mix[0, index[function_of[name]]] += row.BUILDINGS
            mix[1, index[function_of[name]]] += row.BLDG_REPL_COST_USD
        mixes[occupancy.lower()] = mix / mix.sum(1, keepdims=True)

    hexagons = {f"h{r}": np.array([h3.latlng_to_cell(la, lo, r) for la, lo in zip(cells.lat, cells.lon)])
                for r in HEX_RESOLUTIONS}
    np.savez_compressed(HEX_FILE, **hexagons)
    for name, matrix in matrices.items():
        sp.save_npz(FILES[name], matrix.tocsr())
    np.savez(MIX_FILE, **mixes)
    FUNCTIONS_FILE.write_text(json.dumps(functions), encoding="utf-8")
    print(f"engine data: {len(cells)} cells, {len(functions)} vulnerability functions")


def load():
    """-> dict with cells, function ids, residential matrices, commercial/industrial mixes and the H3 cells."""
    mixes = np.load(MIX_FILE)
    hexagons = np.load(HEX_FILE)
    return {
        "cells": pd.read_parquet(PROCESSED / "cells.parquet"),
        "functions": json.loads(FUNCTIONS_FILE.read_text(encoding="utf-8")),
        "matrices": {name: sp.load_npz(path).tocsr() for name, path in FILES.items()},
        "mix": {"com": mixes["com"], "ind": mixes["ind"]},
        "hex": {r: hexagons[f"h{r}"] for r in HEX_RESOLUTIONS},
    }
