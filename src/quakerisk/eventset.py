"""Stochastic event set from the ESHM20 source model (OpenQuake `event_based`, ruptures only).

OpenQuake samples the logic tree (area, smoothed-seismicity and fault sources, activity and maximum magnitude
alternatives) and simulates years of earthquakes. Each sampled logic-tree path (realization) is simulated for `years /
samples` years; all paths are pooled with equal weight. Only the events that can matter for Germany are kept.

ESHM20 models its area and smoothed-seismicity sources as point sources with a default orientation (strike 0, dip 90),
so the output has hypocentre, magnitude and rake but no usable strike and dip; the loss step assigns those.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from .paths import ESHM20_RAW, HAZARD, STATES_GEOJSON

SITE_MODEL = "eshm20_site_model_v06d.csv"
GERMANY_BOX = (5.8, 15.1, 47.2, 55.1)  # lon min / max, lat min / max of the sites that define the region of interest
SITE_STRIDE = 4  # use every 4th site of the 0.1 degree ESHM20 site model (0.4 degree); sites only matter for configuration
TRUNCATION_LEVEL = 3
MAXIMUM_DISTANCE = ("{'Craton': 400.0, 'Subduction Interface': 250.0, 'Subduction Inslab': 300.0, "
                    "'Non-Subduction Deep': 500.0, 'Volcanic': 50.0, 'default': 200.0}")


def keep_distance_km(magnitude):
    """Events further from Germany than this are not expected to cause loss at the 1 km cells."""
    return np.where(magnitude < 5.5, 150.0, np.where(magnitude < 6.5, 300.0, 400.0))


def events_path(name):
    return HAZARD / f"events_{name}.parquet"


def read_events(name):
    """-> (events table, metadata with the number of simulated years)."""
    meta = json.loads((HAZARD / f"events_{name}.json").read_text())
    return pd.read_parquet(events_path(name)), meta


def _write_job(folder: Path, years: int, samples: int) -> Path:
    """job.ini and a reduced site model next to it; the logic trees stay in data/raw/eshm20."""
    folder.mkdir(parents=True, exist_ok=True)
    sites = pd.read_csv(ESHM20_RAW / SITE_MODEL)
    lon0, lon1, lat0, lat1 = GERMANY_BOX
    sites = sites[(sites.lon >= lon0) & (sites.lon <= lon1) & (sites.lat >= lat0) & (sites.lat <= lat1)]
    lats = np.sort(sites.lat.unique())[::SITE_STRIDE]
    lons = np.sort(sites.lon.unique())[::SITE_STRIDE]
    sites[sites.lat.isin(lats) & sites.lon.isin(lons)].to_csv(folder / "site_model.csv", index=False)
    relative = Path("..") / ".." / ".." / "raw" / "eshm20"  # from data/processed/hazard/<name>/ to the ESHM20 inputs
    job = f"""[general]
description = ESHM20 stochastic event set, ruptures only
calculation_mode = event_based
random_seed = 23

[logic_tree]
number_of_logic_tree_samples = {samples}

[erf]
rupture_mesh_spacing = 5
complex_fault_mesh_spacing = 50
width_of_mfd_bin = 0.1
area_source_discretization = 10.0
pointsource_distance = 75
ps_grid_spacing = 50

[site_params]
reference_vs30_type = measured
reference_vs30_value = 800.0
site_model_file = site_model.csv
reference_depth_to_1pt0km_per_sec = 30.0

[calculation]
source_model_logic_tree_file = {(relative / 'source_model_logic_tree_eshm20_model_v12e.xml').as_posix()}
gsim_logic_tree_file = {(relative / 'gmpe_complete_logic_tree_5br.xml').as_posix()}
investigation_time = 1.0
ses_per_logic_tree_path = {years // samples}
ground_motion_fields = false
truncation_level = {TRUNCATION_LEVEL}
maximum_distance = {MAXIMUM_DISTANCE}
intensity_measure_types = PGA

[output]
export_dir = output/
"""
    path = folder / "job.ini"
    path.write_text(job)
    return path


def _run_openquake(job: Path) -> int:
    oq = shutil.which("oq") or str(Path(sys.executable).with_name("oq"))
    done = subprocess.run([oq, "run", job.name], cwd=job.parent, capture_output=True, text=True)
    output = done.stdout + done.stderr
    if done.returncode != 0:
        print(output[-3000:])
        raise RuntimeError("the OpenQuake run failed")
    return int(re.findall(r"calc_(\d+)\.hdf5", output)[-1])


def extract(calc_id: int, years: int, samples: int) -> pd.DataFrame:
    """Events of an OpenQuake event-based calculation as a table, reduced to those near Germany."""
    from openquake.commonlib import datastore

    ds = datastore.read(calc_id)
    ruptures = ds["ruptures"][:]
    hypocentre = ruptures["hypo"]
    table = pd.DataFrame({"rup_id": ruptures["id"], "mag": ruptures["mag"], "rake": ruptures["rake"],
                          "lon": hypocentre[:, 0], "lat": hypocentre[:, 1], "depth": hypocentre[:, 2]})
    events = pd.DataFrame(ds["events"][:]).rename(columns={"id": "event_id"})
    table = events[["event_id", "rup_id", "rlz_id", "year"]].merge(table, on="rup_id").drop(columns="rup_id")
    assert table.year.between(1, years).all() and table.rlz_id.nunique() == samples, "unexpected event table"

    # distance of the epicentre to Germany (km), to drop the events that cannot cause loss
    germany = gpd.read_file(STATES_GEOJSON).to_crs(3035).dissolve().geometry.iloc[0]
    wide = table[(table.lon > -5) & (table.lon < 25) & (table.lat > 40) & (table.lat < 62)]
    points = gpd.GeoSeries(gpd.points_from_xy(wide.lon, wide.lat), crs=4326).to_crs(3035)
    table = wide.assign(km_to_germany=(points.distance(germany) / 1000).to_numpy())
    table = table[table.km_to_germany <= keep_distance_km(table.mag.to_numpy())]
    return table.sort_values("year").reset_index(drop=True)


def generate(years: int = 20000, samples: int = 100, name: str = "eventset") -> Path:
    """Sample `years` simulated years from the ESHM20 source model and save the events near Germany."""
    if years % samples:
        raise ValueError("years must be a multiple of samples")
    folder = HAZARD / f"job_{name}"
    job = _write_job(folder, years, samples)
    print(f"sampling {years} years ({samples} logic-tree paths x {years // samples} years) with OpenQuake ...")
    calc_id = _run_openquake(job)
    table = extract(calc_id, years, samples)
    HAZARD.mkdir(parents=True, exist_ok=True)
    table.to_parquet(events_path(name), index=False)
    (HAZARD / f"events_{name}.json").write_text(json.dumps({"years": years, "samples": samples, "openquake_calc": calc_id}))
    print(f"{len(table)} events near Germany kept (OpenQuake calculation {calc_id}); "
          f"M>=5: {(table.mag >= 5).sum()}, M>=6: {(table.mag >= 6).sum()}, largest M{table.mag.max():.2f}")
    return events_path(name)
