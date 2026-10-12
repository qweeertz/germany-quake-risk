import numpy as np
import pandas as pd
import scipy.sparse as sp

from quakerisk.paths import GEM_RAW, PROCESSED
from quakerisk.vulnerability import parse_taxonomy


def test_exposure_reproduces_gem_totals():
    cells = pd.read_parquet(PROCESSED / "cells.parquet")
    adm1 = pd.read_csv(GEM_RAW / "Exposure_Summary_Adm1.csv")
    for occupancy, key in (("RES", "res"), ("COM", "com"), ("IND", "ind")):
        gem = adm1[adm1.OCCUPANCY == occupancy].set_index("ID_1")
        per_state = cells.groupby("state")[[f"{key}_bldg", f"{key}_bldg_cost"]].sum()
        assert np.allclose(per_state[f"{key}_bldg"], gem.BUILDINGS.reindex(per_state.index), rtol=1e-6)
        assert np.allclose(per_state[f"{key}_bldg_cost"], gem.BLDG_REPL_COST_USD.reindex(per_state.index), rtol=1e-6)


def test_every_cell_has_a_state_and_settlement():
    cells = pd.read_parquet(PROCESSED / "cells.parquet")
    assert cells.state.notna().all() and cells.state.nunique() == 16
    assert set(cells.settlement) == {"BIG_CITY", "URBAN", "RURAL"}


def test_class_matrix_matches_residential_buildings():
    cells = pd.read_parquet(PROCESSED / "cells.parquet")
    counts = sp.load_npz(PROCESSED / "cell_taxonomy.npz")
    per_cell = np.asarray(counts.sum(1)).ravel()
    assert np.allclose(per_cell, cells.res_bldg, rtol=1e-3, atol=1e-3)


def test_every_exposure_class_is_mapped_to_a_vulnerability_function():
    mapping = pd.read_csv(PROCESSED / "taxonomy_vulnerability_map.csv")
    taxonomy = pd.read_csv(GEM_RAW / "Exposure_Summary_Taxonomy.csv")
    assert set(mapping.taxonomy) == set(taxonomy.TAXONOMY)
    assert mapping.score.max() < 6  # no poor matches


def test_taxonomy_parser():
    parsed = parse_taxonomy("MUR+CL/LWAL/CDN+ERN/H:2/RES")
    assert parsed["family"] == "MUR" and parsed["design"] == 0 and parsed["storeys"] == 2 and parsed["occupancy"] == "RES"


def test_industrial_buildings_follow_industrial_land():
    cells = pd.read_parquet(PROCESSED / "cells.parquet")
    on_industrial_land = cells.loc[cells.ind_land_m2 > 0, "ind_bldg"].sum()
    assert on_industrial_land >= 0.89 * cells.ind_bldg.sum()  # 90 % follow the land, 10 % a population baseline
    assert cells.ind_land_m2.max() <= 1e6 + 1  # never more than one square kilometre in a 1 km cell
    assert 5e9 < cells.ind_land_m2.sum() < 8e9  # about 6,300 km2 of CORINE industrial land lie in the grid cells
