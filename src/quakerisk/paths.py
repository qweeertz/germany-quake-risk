"""Locations of data and web files (relative to the repository root)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
WEB = ROOT / "web"

GEM_RAW = RAW / "gem"
ZENSUS_RAW = RAW / "zensus"
STATES_GEOJSON = RAW / "gisco" / "germany_states.geojson"

# vulnerability categories of the GEM model and the files that hold them
VULNERABILITY_FILES = {
    "structural": "vulnerability_structural.xml",
    "nonstructural": "vulnerability_nonstructural.xml",
    "contents": "vulnerability_contents.xml",
    "fatalities": "vulnerability_fatalities.xml",
}
