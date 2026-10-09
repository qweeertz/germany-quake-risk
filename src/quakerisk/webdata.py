"""Build the web page: aggregate the 1 km cells to H3 hexagons and embed data, styles and script in web/index.html.

web/template.html, web/style.css and web/app.js are the sources; web/index.html is generated and not tracked.
"""
import json

import geopandas as gpd
import numpy as np
import pandas as pd
import scipy.sparse as sp

from .engine_data import HEX_FILE, HEX_RESOLUTIONS
from .exposure import NUTS1_TO_ISO
from .paths import PROCESSED, STATES_GEOJSON, WEB

# Layers shown on the exposure map: total / residential / commercial / industrial value (v*) and buildings (b*), residents.
MAP_KEYS = ["value", "bldg", "occ", "vres", "vcom", "vind", "bres", "bcom", "bind"]
STATE_NAMES = {"DE-BW": "Baden-Württemberg", "DE-BY": "Bayern", "DE-BE": "Berlin", "DE-BB": "Brandenburg",
               "DE-HB": "Bremen", "DE-HH": "Hamburg", "DE-HE": "Hessen", "DE-MV": "Mecklenburg-Vorpommern",
               "DE-NI": "Niedersachsen", "DE-NW": "Nordrhein-Westfalen", "DE-RP": "Rheinland-Pfalz",
               "DE-SL": "Saarland", "DE-SN": "Sachsen", "DE-ST": "Sachsen-Anhalt", "DE-SH": "Schleswig-Holstein",
               "DE-TH": "Thüringen"}
NUTS_OF = {iso: nuts for nuts, iso in NUTS1_TO_ISO.items()}


def _cell_columns():
    """Cell table with the map quantities and the residential class shares used in the tooltip."""
    cells = pd.read_parquet(PROCESSED / "cells.parquet")
    counts = sp.load_npz(PROCESSED / "cell_taxonomy.npz").tocsr()
    taxonomies = json.loads((PROCESSED / "taxonomies.json").read_text(encoding="utf-8"))
    attributes = np.array(taxonomies["attributes_material_code_height"])
    materials = taxonomies["materials"]

    def share_matrix(column, n_values):
        return sp.csr_matrix((np.ones(len(attributes)), (np.arange(len(attributes)), attributes[:, column])),
                             shape=(len(attributes), n_values))

    by_material = (counts @ share_matrix(0, len(materials))).toarray()  # residential buildings per material
    by_design = (counts @ share_matrix(1, 4)).toarray()  # ... per seismic design level (none, low, moderate, high)

    cells["value"] = (cells.res_bldg_cost + cells.res_contents_cost + cells.com_bldg_cost + cells.com_contents_cost
                      + cells.ind_bldg_cost + cells.ind_contents_cost)
    cells["bldg"] = cells.res_bldg + cells.com_bldg + cells.ind_bldg
    cells["vres"] = cells.res_bldg_cost + cells.res_contents_cost
    cells["vcom"] = cells.com_bldg_cost + cells.com_contents_cost
    cells["vind"] = cells.ind_bldg_cost + cells.ind_contents_cost
    cells["bres"], cells["bcom"], cells["bind"] = cells.res_bldg, cells.com_bldg, cells.ind_bldg
    cells["occ"] = cells.res_occupants
    cells["r_cdn"] = by_design[:, 0]
    for key, material in (("r_mur", "MUR"), ("r_w", "W"), ("r_cr", "CR")):
        cells[key] = by_material[:, materials.index(material)]
    return cells


def _hexagons(cells, resolution, hex_ids):
    """Aggregate the cells of one H3 resolution; returns the data block for the page."""
    cells = cells.assign(hex=hex_ids)
    grouped = cells.groupby("hex")[MAP_KEYS + ["r_cdn", "r_mur", "r_w", "r_cr"]].sum()
    grouped = grouped[grouped.bldg > 0.5]
    residential = grouped.bres.clip(lower=1e-9)
    columns = {k: ((grouped[k] / 1e6).round(1) if k.startswith("v") else grouped[k].round(1)) for k in MAP_KEYS}
    columns.update({"cdn": (100 * grouped.r_cdn / residential).round(0), "mur": (100 * grouped.r_mur / residential).round(0),
                    "w": (100 * grouped.r_w / residential).round(0), "cr": (100 * grouped.r_cr / residential).round(0)})
    breaks = {}
    for key in MAP_KEYS:  # skewed quantities: log-spaced colour classes
        positive = columns[key][columns[key] > 0]
        breaks[key] = [float(v) for v in np.geomspace(np.quantile(positive, 0.10), np.quantile(positive, 0.995), 6)]
    print(f"  H3 resolution {resolution}: {len(grouped)} hexagons")
    return {"ids": list(grouped.index), **{k: v.astype(float).tolist() for k, v in columns.items()}, "breaks": breaks}


def build():
    cells = _cell_columns()
    hexagons = np.load(HEX_FILE)
    data = {"keys": MAP_KEYS}
    for resolution in HEX_RESOLUTIONS:
        data[f"r{resolution}"] = _hexagons(cells, resolution, hexagons[f"h{resolution}"])

    states = cells.groupby("state")[MAP_KEYS].sum().reset_index()
    states["name"] = states.state.map(STATE_NAMES)
    states["nuts"] = states.state.map(NUTS_OF)
    data["states"] = json.loads(states.round(0).to_json(orient="records"))
    data["totals"] = {"value": float(cells.value.sum()), "bldg": float(cells.bldg.sum()), "occ": float(cells.occ.sum())}

    outline = gpd.read_file(STATES_GEOJSON)[["NUTS_ID", "geometry"]]
    outline["geometry"] = outline.geometry.simplify(0.01)
    data["outline"] = json.loads(outline.to_json())

    page = (WEB / "template.html").read_text(encoding="utf-8")
    page = page.replace("/*__STYLE__*/", (WEB / "style.css").read_text(encoding="utf-8"))
    page = page.replace("/*__SCRIPT__*/", (WEB / "app.js").read_text(encoding="utf-8"))
    page = page.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    (WEB / "index.html").write_text(page, encoding="utf-8")
    print(f"  web/index.html written ({(WEB / 'index.html').stat().st_size / 1e6:.1f} MB)")
