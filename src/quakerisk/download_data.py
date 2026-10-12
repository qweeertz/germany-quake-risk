"""Download the open input data (about 100 MB) into data/raw/.

Sources
  * GEM Global Exposure Model v2026.0.0, Germany summaries (CC BY-NC-SA 4.0)
  * GEM Global Vulnerability Model v2026.0.0, Germany (CC BY-NC-SA 4.0)
  * Zensus 2022 grid data, Statistische Aemter des Bundes und der Laender (1 km grid tables)
  * Eurostat / GISCO NUTS 2021 boundaries (reduced to the German federal states)
  * BKG CORINE Land Cover 5 ha (CLC5) 2018, only the industrial polygons (WFS query with a class filter, about 40 MB;
    the full product is a 1-3 GB download)
"""
import io
import json
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .paths import CLC_INDUSTRIAL, GEM_RAW, STATES_GEOJSON, VULNERABILITY_FILES, ZENSUS_RAW

GEM_EXPOSURE = "https://raw.githubusercontent.com/gem/global_exposure_model/v2026.0.0/Europe/Germany/summaries/"
GEM_VULNERABILITY = "https://raw.githubusercontent.com/gem/global_vulnerability_model/v2026.0.0/Europe/Germany/"
ZENSUS = "https://www.destatis.de/static/DE/zensus/gitterdaten/"
NUTS1 = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_01M_2021_4326_LEVL_1.geojson"

CLC_WFS = "https://sg.geodatenzentrum.de/wfs_clc5_2018"
CLC_CLASSES = ("121", "123", "124")  # industrial or commercial units, port areas, airports
CLC_PAGE = 5000

# local name -> (zip file, member of the zip that holds the 1 km table)
ZENSUS_TABLES = {
    "population_1km.csv": ("Zensus2022_Bevoelkerungszahl.zip", "Zensus2022_Bevoelkerungszahl_1km-Gitter.csv"),
    "buildings_age_1km.csv": ("Gebaeude_nach_Baujahr_Jahrzehnte.zip", "Zensus2022_Baujahr_JZ_1km-Gitter.csv"),
    "buildings_type_1km.csv": ("Gebaeude_mit_Wohnraum_nach_Gebaeudetyp_Groesse.zip",
                               "Zensus2022_Geb_Gebaeudetyp_Groesse_1km-Gitter.csv"),
}


def _fetch(url: str) -> bytes:
    print(f"  downloading {url if len(url) < 110 else url[:90] + ' ...'}")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (quakerisk data download)"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def _needs(path: Path, force: bool) -> bool:
    if path.exists() and not force:
        print(f"  {path.name}: already there")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    return True


def _fetch_clc_industrial():
    """Industrial CLC5 2018 polygons from the BKG web feature service (EPSG:25832), following the pages of the service."""
    import geopandas as gpd

    literals = "".join(f"<fes:PropertyIsEqualTo><fes:ValueReference>clc18</fes:ValueReference><fes:Literal>{c}</fes:Literal>"
                       "</fes:PropertyIsEqualTo>" for c in CLC_CLASSES)
    flt = f'<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0"><fes:Or>{literals}</fes:Or></fes:Filter>'
    base = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "clc5_2018:clc5", "FILTER": flt}
    query = lambda **extra: CLC_WFS + "?" + urllib.parse.urlencode({**base, **extra})  # noqa: E731
    hits = _fetch(query(RESULTTYPE="hits")).decode("utf-8")
    expected = int(hits.split('numberMatched="')[1].split('"')[0])
    features = {}
    while len(features) < expected:
        page = json.loads(_fetch(query(OUTPUTFORMAT="application/json", COUNT=CLC_PAGE, STARTINDEX=len(features))))
        new = {f["id"]: f for f in page["features"]}
        if not new or all(i in features for i in new):
            raise RuntimeError("the web feature service returned no new polygons")
        features.update(new)
    if len(features) != expected:
        raise RuntimeError(f"expected {expected} polygons, got {len(features)}")
    polygons = gpd.GeoDataFrame.from_features(list(features.values()), crs=25832)[["clc18", "geometry"]]
    CLC_INDUSTRIAL.parent.mkdir(parents=True, exist_ok=True)
    polygons.to_parquet(CLC_INDUSTRIAL)
    print(f"  {len(polygons)} industrial polygons")


def download(force: bool = False) -> None:
    print("GEM exposure and vulnerability")
    for name in ("Exposure_Summary_Adm1.csv", "Exposure_Summary_Taxonomy.csv"):
        if _needs(GEM_RAW / name, force):
            (GEM_RAW / name).write_bytes(_fetch(GEM_EXPOSURE + name))
    for name in VULNERABILITY_FILES.values():
        if _needs(GEM_RAW / name, force):
            (GEM_RAW / name).write_bytes(_fetch(GEM_VULNERABILITY + name))

    print("Zensus 2022 grid tables")
    for local, (archive, member) in ZENSUS_TABLES.items():
        if _needs(ZENSUS_RAW / local, force):
            with zipfile.ZipFile(io.BytesIO(_fetch(ZENSUS + archive))) as zf:  # read one member, never extract paths
                name = next(n for n in zf.namelist() if n.split("/")[-1] == member)  # some archives use a subfolder
                (ZENSUS_RAW / local).write_bytes(zf.read(name))

    print("State boundaries")
    if _needs(STATES_GEOJSON, force):
        collection = json.loads(_fetch(NUTS1))
        collection["features"] = [f for f in collection["features"] if f["properties"]["CNTR_CODE"] == "DE"]
        STATES_GEOJSON.write_text(json.dumps(collection), encoding="utf-8")
    print("CORINE land cover (industrial polygons)")
    if _needs(CLC_INDUSTRIAL, force):
        _fetch_clc_industrial()
    print("done")
