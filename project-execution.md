# Project execution log

How the Germany earthquake risk dashboard was built, step by step: decisions, findings, assumptions and caveats.
For what the project is and how to run it, see [README.md](README.md).

**Plan:** GEM exposure of Germany -> disaggregate to a 1 km grid -> hazard (OpenQuake hazardlib GMPEs) and vulnerability
-> fast loss engine and API (FastAPI) -> single-page map dashboard. Steps: 1 environment check, 2 inspect the GEM data,
3 disaggregated exposure map, 4 loss engine, 5 interactive dashboard, 6 validation against the OpenQuake engine,
7 cleanup and packaging.

## Step 1: environment
- Windows, Python 3.12 virtual environment. `pip install openquake.engine` fails on Windows because GDAL needs a C++
  compiler; the engine package is therefore installed with `--no-deps` and its other dependencies are installed by hand
  (see `pyproject.toml`). The hazard library and even the full engine run without GDAL.
- A first test (M5.8 ground motion at 50,000 sites) confirmed that hazardlib works.

## Step 2: what GEM provides for Germany
- GEM exposure model v2026.0.0 (public repo `gem/global_exposure_model`, `Europe/Germany/summaries`): only the Adm0, Adm1
  and taxonomy summary tables. The 1 km disaggregated asset files need a licence request
  (<https://www.globalquakemodel.org/license-request/global-exposure-model>); we decided to disaggregate ourselves.
- Adm1 table: per federal state and occupancy (residential, commercial, industrial). Taxonomy table: national, per
  settlement type (big city, urban, rural). There is no taxonomy-by-state table.
- Vulnerability model (`gem/global_vulnerability_model`, `Europe/Germany`): structural, non-structural, contents and
  fatality functions (82 each) for PGA, SA(0.3), SA(0.6) and SA(1.0). **No fragility functions** are public.
- The exposure has 169 taxonomy strings, the vulnerability model 82 ids; a direct match covers only 7 % of the buildings.
  GEM's mapping file (`Vulnerability_mapping_DEU.csv`) is not in the public repositories, so the mapping is rebuilt (step 4).

## Step 3: disaggregated exposure
**Data.** Zensus 2022 grid tables (1 km, EPSG:3035): population, buildings by construction decade, buildings by type and
size (193k-210k cells; small counts are suppressed and values are rounded) and the NUTS 2021 state boundaries.
Downloaded by `quakerisk download`.

**Method** (`src/quakerisk/exposure.py`):
- 211,893 cells. Residential buildings: Zensus counts, with suppressed categories filled from the mix of the surrounding
  10 km block, scaled to the GEM state totals (scale factors 0.96-1.04, i.e. the two sources agree well).
- Settlement type per cell from the population density, calibrated to the GEM national shares (big city 5 %, urban 53 %,
  rural 42 % of the residential buildings).
- Residential taxonomy (77 GEM classes) by iterative proportional fitting per settlement type: the prior follows the
  construction decade (seismic design level, material; first German seismic code DIN 4149 in 1957, revised 1981 and 2005)
  and the Zensus building type (number of storeys); the margins reproduce the GEM class counts exactly.
- Commercial and industrial buildings: the Zensus covers only buildings with dwellings, so the GEM state totals are
  spread by population (commercial weighted 2.0 / 1.0 / 0.6 by settlement type). Planned refinement: CORINE land cover for
  industry.
- Residential occupants from the Zensus population, scaled to the GEM state totals.

**Limitations.** Commercial and industrial buildings have one national class mix (not per cell); GEM gives no
commercial or industrial occupants; the settlement class uses the 1 km density only; the class mix per cell is modelled,
not observed.

**Map** (`src/quakerisk/webdata.py`, `web/`): cells aggregated to H3 hexagons (resolution 6 nationally, 7 when zoomed in),
layers for value, buildings and residents (total, residential, commercial, industrial), hover tooltip, state table.
No map tiles are used (state outlines and city labels only); the page needs internet only for the MapLibre GL and h3-js
scripts.

## Step 4: scenario loss engine
**Class mapping** (`vulnerability.py`): 169 exposure classes -> 63 of the 82 vulnerability functions by attribute
distance (material family, structural system, seismic design level, ductility, height, occupancy); 89 % of the buildings
match exactly or closely, 11 % approximately, none poorly.

**Hazard** (`hazard.py`): planar rupture (hazardlib, Wells & Coppersmith), Joyner-Boore distance per 1 km cell. Default
ground-motion model: equal-weight ensemble of Akkar 2014, Bindi 2014, Cauzzi 2014 and Lanzano 2019, Vs30 = 400 m/s
everywhere. The ESHM20 backbone (Kotha et al. 2020) is selectable; it gives about 1.7-2.8 times higher motion for
M5-6 events and about twice the loss. (It was the first choice, but it over-predicted the 1978 Albstadt event strongly.)

**Loss** (`engine.py`): expected loss ratio per vulnerability function, integrated over the lognormal ground-motion
variability (21-point Gauss-Legendre quadrature on the normal truncated at 3 sigma) with lookup tables; 0.2-0.8 s per
scenario. Components: structural, non-structural, contents; fatalities = residents x fatality ratio.

**Assumptions.**
- Building cost split structural / non-structural: 30 / 70 % residential, 25 / 75 % commercial and industrial (GEM's
  summaries do not give it).
- Fatalities: night-time, residents only (no commercial or industrial occupants available).
- Damage grades are derived from the beta loss-ratio distribution of the structural functions (bands 2-10 / 10-30 /
  30-70 / above 70 %), because there are no fragility curves. The distribution is U-shaped, so "complete" can exceed
  "extensive".

**Calibration caveat.** For the 1978 Albstadt event (M5.7; from memory roughly DM 275 million of damage, no deaths) the
ensemble gives about USD 2 billion and about 100 fatalities. Treat the results as order-of-magnitude scenario estimates,
good for comparing scenarios, not as forecasts.

## Step 5: interactive dashboard
- `quakerisk serve` starts FastAPI at <http://localhost:8000>: `POST /api/scenario` runs the engine and returns the summary
  plus hexagon layers (about 1 s per call); `GET /` serves the page.
- Scenario tab: click the map or pick an example to set the epicentre (the marker can be dragged); magnitude 4.5-8.0,
  depth, strike, fault type and ground-motion model; results update automatically. Layers: shaking intensity (MMI),
  loss, loss ratio, fatalities. Panel: loss, fatalities, heavily damaged buildings, people at MMI VI or more, damage
  grades, loss by component, building type and state.
- Dark theme by default (remembered in the browser). The magnitude slider stops at M8, the limit of the ground-motion
  models, with a marker and a warning above M7 (the end of the realistic range for Germany). The rupture width is limited
  to the crust and its length to 1000 km.
- Opened without the backend, the exposure tab still works and the scenario tab says the backend is not reachable.

## Step 6: validation against the OpenQuake engine
See [validation/README.md](validation/README.md). With identical inputs the fast engine reproduces OpenQuake's
`scenario_risk` to within 2-3 % (Monte-Carlo error 2-3 %) on two scenarios. The validation also exposed an error in the
first version of the quadrature (variability clipped instead of truncated at 3 sigma, about 6 % too high), which was fixed.
OpenQuake runs natively on Windows without GDAL, but needs numpy 2.2.6, numba 0.61.2 and pandas 2.2.3 (pandas 3 breaks
it), django below 5, geopandas 1.1.x and decorator 5.1.x; these pins are in `pyproject.toml`.

## Step 7: cleanup and packaging
- The scripts became the installable package `quakerisk` (`src/quakerisk`) with one command line (`quakerisk`).
- `quakerisk download` replaces the manual downloads (pinned release tags of the GEM repositories).
- The web page is split into `web/template.html`, `web/style.css` and `web/app.js`; `quakerisk web` inlines them with the data.
- Throw-away files (patch scripts, preview plot, backup template, unused NUTS3 boundaries) were removed; the smoke scripts
  became pytest tests; the code passes `ruff`.
- A regression check confirmed that the refactoring changed no numbers (cells, class matrix, mapping and three scenarios
  identical to the previous version).

## Attribution and licences
- **Exposure model:** Yepes-Estrada, C. et al. (2026), *Global Exposure Model* (v2026.0.0), GEM Foundation.
  <https://doi.org/10.5281/zenodo.8117363>. Licence CC BY-NC-SA 4.0 (<https://creativecommons.org/licenses/by-nc-sa/4.0/>);
  derived data is therefore shared under the same licence, for non-commercial use only.
- **Vulnerability model:** Nafeh, A.M.B., Aljawhari, K., Silva, V. (2025), *The 2025 Global Vulnerability Model of the GEM
  Foundation*. <https://github.com/gem/global_vulnerability_model> (CC BY-NC-SA 4.0).
- **Census grid data:** Source: Statistische Ämter des Bundes und der Länder, Zensus 2022 (<https://www.zensus2022.de>).
  Aggregated and modified in this project. The exact licence text of the grid download was not verified; Destatis
  publishes related products under Datenlizenz Deutschland - Namensnennung 2.0.
- **Administrative boundaries:** (c) EuroGeographics for the administrative boundaries; Eurostat / GISCO NUTS 2021
  (<https://ec.europa.eu/eurostat/web/gisco>). Non-commercial use with attribution.
