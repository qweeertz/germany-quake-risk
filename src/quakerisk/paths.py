"""Locations of data and web files (relative to the repository root)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
WEB = ROOT / "web"
HAZARD = PROCESSED / "hazard"  # stochastic event sets, event loss tables and risk metrics

GEM_RAW = RAW / "gem"
ZENSUS_RAW = RAW / "zensus"
STATES_GEOJSON = RAW / "gisco" / "germany_states.geojson"
CLC_INDUSTRIAL = RAW / "clc" / "clc5_2018_industrial.parquet"  # CORINE CLC5 2018 polygons of industrial land
ESHM20_RAW = RAW / "eshm20"  # ESHM20 OpenQuake input files (main region): config, logic trees, source models

# vulnerability categories of the GEM model and the files that hold them
VULNERABILITY_FILES = {
    "structural": "vulnerability_structural.xml",
    "nonstructural": "vulnerability_nonstructural.xml",
    "contents": "vulnerability_contents.xml",
    "fatalities": "vulnerability_fatalities.xml",
}
