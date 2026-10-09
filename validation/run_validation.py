"""Run one scenario through the fast engine and through the OpenQuake engine (scenario_risk) with identical inputs.

Same planar rupture, same exposure assets (cell x vulnerability function), same GEM vulnerability functions (mean loss
ratios, ignore_covs), one GMPE (Akkar et al. 2014), Vs30 = 400 m/s, truncation level 3. OpenQuake samples N ground-motion
fields (inter- plus intra-event variability, no spatial correlation); the fast engine integrates the same variability
analytically, so expected values agree up to Monte-Carlo error. Compare the two with compare.py.

Usage:  python validation/run_validation.py --name duren_m58 [--lon 6.5 --lat 50.8 --mag 5.8 --events 500]
        --skip-openquake re-runs only the fast engine for an existing case.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp

from quakerisk.engine import STRUCTURAL_SHARE, QuakeEngine
from quakerisk.paths import GEM_RAW, ROOT, VULNERABILITY_FILES

VALIDATION = ROOT / "validation"
GMPE = "akkar"                # model name in the fast engine ...
GMPE_OPENQUAKE = "AkkarEtAlRjb2014"  # ... and the same model in OpenQuake


def build_assets(engine, cells):
    """Assets (cell x vulnerability function) of the shaken cells, merged over the three occupancies."""
    matrices = {k: m[cells] for k, m in engine.matrices.items()}
    share = STRUCTURAL_SHARE
    structural = matrices["building_value"] * share["res"]
    nonstructural = matrices["building_value"] * (1 - share["res"])
    contents = matrices["contents_value"].copy()
    number = matrices["buildings"].copy()
    for occupancy in ("com", "ind"):
        count_mix, value_mix = engine.mix[occupancy]
        building_cost = engine.cells[f"{occupancy}_bldg_cost"].to_numpy()[cells]
        contents_cost = engine.cells[f"{occupancy}_contents_cost"].to_numpy()[cells]
        n_buildings = engine.cells[f"{occupancy}_bldg"].to_numpy()[cells]
        structural = structural + sp.csr_matrix(np.outer(building_cost * share[occupancy], value_mix))
        nonstructural = nonstructural + sp.csr_matrix(np.outer(building_cost * (1 - share[occupancy]), value_mix))
        contents = contents + sp.csr_matrix(np.outer(contents_cost, value_mix))
        number = number + sp.csr_matrix(np.outer(n_buildings, count_mix))

    columns = {}
    for name, matrix in {"structural": structural, "nonstructural": nonstructural, "contents": contents,
                         "number": number, "night": matrices["residents"]}.items():
        matrix = matrix.tocoo()
        columns[name] = pd.Series(matrix.data, index=pd.MultiIndex.from_arrays([matrix.row, matrix.col]))
    assets = pd.concat(columns, axis=1).fillna(0.0)
    assets.index.names = ["cell", "func"]
    assets = assets.reset_index()
    assets = assets[(assets.structural + assets.nonstructural + assets.contents) > 0].copy()
    assets["lon"] = engine.cells.lon.to_numpy()[cells][assets.cell]
    assets["lat"] = engine.cells.lat.to_numpy()[cells][assets.cell]
    assets["taxonomy"] = [engine.functions[i] for i in assets.func]
    assets["id"] = [f"a{c}_{f}" for c, f in zip(assets.cell, assets.func)]
    return assets


def _corner(point):
    return f'lon="{point.longitude:.6f}" lat="{point.latitude:.6f}" depth="{point.depth:.4f}"'


def write_openquake_job(folder, surface, scenario, assets, events):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rupture.xml").write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<nrml xmlns="http://openquake.org/xmlns/nrml/0.4" xmlns:gml="http://www.opengis.net/gml">
  <singlePlaneRupture>
    <magnitude>{scenario["mag"]}</magnitude>
    <rake>{scenario["rake"]}</rake>
    <hypocenter lon="{scenario["lon"]}" lat="{scenario["lat"]}" depth="{scenario["depth"]}"/>
    <planarSurface strike="{surface.strike:.6f}" dip="{surface.dip:.6f}">
      <topLeft {_corner(surface.top_left)}/>
      <topRight {_corner(surface.top_right)}/>
      <bottomLeft {_corner(surface.bottom_left)}/>
      <bottomRight {_corner(surface.bottom_right)}/>
    </planarSurface>
  </singlePlaneRupture>
</nrml>''')
    assets[["id", "lon", "lat", "taxonomy", "number", "structural", "nonstructural", "contents", "night"]].to_csv(
        folder / "exposure.csv", index=False, float_format="%.6g")
    (folder / "exposure.xml").write_text('''<?xml version="1.0" encoding="utf-8"?>
<nrml xmlns="http://openquake.org/xmlns/nrml/0.5">
  <exposureModel id="validation" category="buildings" taxonomySource="GEM taxonomy v4">
    <description>validation exposure</description>
    <conversions>
      <costTypes>
        <costType name="structural" type="aggregated" unit="USD"/>
        <costType name="nonstructural" type="aggregated" unit="USD"/>
        <costType name="contents" type="aggregated" unit="USD"/>
      </costTypes>
    </conversions>
    <occupancyPeriods>night</occupancyPeriods>
    <assets>exposure.csv</assets>
  </exposureModel>
</nrml>''')
    loss_type = {"structural": "structural", "nonstructural": "nonstructural", "contents": "contents",
                 "fatalities": "occupants"}  # OpenQuake calls the fatality loss type "occupants"
    for category, filename in VULNERABILITY_FILES.items():
        shutil.copy(GEM_RAW / filename, folder / f"vulnerability_{loss_type[category]}.xml")
    (folder / "job.ini").write_text(f'''[general]
description = validation scenario
calculation_mode = scenario_risk
random_seed = 42
[Rupture]
rupture_model_file = rupture.xml
rupture_mesh_spacing = 2.0
[Exposure]
exposure_file = exposure.xml
[Site]
reference_vs30_type = measured
reference_vs30_value = {scenario["vs30"]}
reference_depth_to_2pt5km_per_sec = 1.0
reference_depth_to_1pt0km_per_sec = 50.0
[Vulnerability]
structural_vulnerability_file = vulnerability_structural.xml
nonstructural_vulnerability_file = vulnerability_nonstructural.xml
contents_vulnerability_file = vulnerability_contents.xml
occupants_vulnerability_file = vulnerability_occupants.xml
[Hazard]
gsim = {GMPE_OPENQUAKE}
truncation_level = 3
number_of_ground_motion_fields = {events}
maximum_distance = 1000
[Risk]
ignore_covs = true
asset_correlation = 0
master_seed = 42
''')



def run_openquake(folder):
    """Run `oq run job.ini` in the folder and return the calculation id."""
    oq = shutil.which("oq") or str(Path(sys.executable).with_name("oq"))
    completed = subprocess.run([oq, "run", "job.ini"], cwd=folder, capture_output=True, text=True)
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        print(output[-3000:])
        raise SystemExit("OpenQuake run failed")
    return int(re.findall(r"calc_(\d+)\.hdf5", output)[-1])


def save_fast_engine_results(folder, result):
    np.savez(folder / "ours_components.npz", sel=result.cells, rjb=result.rjb, gm=result.ground_motion, sig=result.sigma,
             **{f"{o}_{k}": result.results[o][k] for o in result.results
                for k in ("structural", "nonstructural", "contents", "fatalities")})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="duren_m58")
    parser.add_argument("--lon", type=float, default=6.5)
    parser.add_argument("--lat", type=float, default=50.8)
    parser.add_argument("--mag", type=float, default=5.8)
    parser.add_argument("--depth", type=float, default=10.0)
    parser.add_argument("--strike", type=float, default=135.0)
    parser.add_argument("--events", type=int, default=500, help="ground-motion fields sampled by OpenQuake")
    parser.add_argument("--skip-openquake", action="store_true")
    args = parser.parse_args()

    folder = VALIDATION / args.name
    if args.skip_openquake:
        scenario = json.loads((folder / "meta.json").read_text())["scenario"]
    else:
        scenario = dict(lon=args.lon, lat=args.lat, mag=args.mag, depth=args.depth, strike=args.strike, dip=60.0,
                        rake=-90.0, vs30=400.0)
    engine = QuakeEngine()
    result = engine.run(scenario["lon"], scenario["lat"], scenario["mag"], scenario["depth"], scenario["strike"],
                        scenario["dip"], scenario["rake"], scenario["vs30"], gmpe=GMPE)
    print(f"fast engine: {len(result.cells)} shaken cells, loss {result.summary()['loss_usd'] / 1e9:.3f} bn USD")
    folder.mkdir(parents=True, exist_ok=True)
    save_fast_engine_results(folder, result)
    if args.skip_openquake:
        return

    assets = build_assets(engine, result.cells)
    print(f"{len(assets)} assets for OpenQuake")
    write_openquake_job(folder, result.surface, scenario, assets, args.events)
    assets.to_parquet(folder / "assets_ours.parquet")
    print("running the OpenQuake engine ...")
    calc_id = run_openquake(folder)
    (folder / "meta.json").write_text(json.dumps({"calc_id": calc_id, "scenario": scenario, "events": args.events}))
    print("OpenQuake calculation", calc_id)


if __name__ == "__main__":
    main()
