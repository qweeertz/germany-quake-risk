// Dashboard logic. `DATA` (hexagon aggregates, state table, outlines) is embedded by `quakerisk web`.
// Sections: constants and formatting, exposure view, map setup, hover, scenario view, start-up.

// ===================================================================================================
// Constants and formatting
// ===================================================================================================
const $ = id => document.getElementById(id);
const HEX_SPLIT_ZOOM = 7.2;          // below: H3 resolution 6 (~36 km2), above: resolution 7 (~5 km2)
const GERMANY_BOUNDS = [[5.5, 47.1], [15.3, 55.2]];
const API = location.port === "8000" ? "" : "http://localhost:8000";
const ORANGE = "#eb6834", RED = "#d03b3b";

// Sequential ramps (light -> dark), reversed on the dark theme so that high values are bright.
const BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
const ORANGE_RAMP = ["#fde9dd", "#fac8ad", "#f4a47c", "#eb6834", "#c24a1d", "#8f3512", "#5c230c"];
const ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"];

const isDark = () => document.documentElement.dataset.theme === "dark";
const ramp = colors => (isDark() ? colors.slice().reverse() : colors);
const toRoman = mmi => ROMAN[Math.max(1, Math.min(10, Math.round(+mmi)))];

function fmtN(v) {
  if (v < 10) return v.toFixed(1);
  if (v >= 1e6) return (v / 1e6).toFixed(1) + " M";
  if (v >= 1e4) return Math.round(v / 1e3) + " k";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + " k";
  return String(Math.round(v));
}
function fmtUSD(v) {
  if (v >= 1e12) return (v / 1e12).toFixed(1) + " tn";
  if (v >= 1e9) return (v / 1e9).toFixed(1) + " bn";
  if (v >= 1e7) return Math.round(v / 1e6) + " m";
  if (v >= 1e6) return (v / 1e6).toFixed(1) + " m";
  if (v >= 1e3) return Math.round(v / 1e3) + " k";
  return String(Math.round(v));
}
function fmtSmall(v) { return v >= 10 ? fmtN(v) : v >= 1 ? v.toFixed(1) : v >= 0.1 ? v.toFixed(2) : v.toFixed(3); }

const hexLabel = res => (res === 7 ? "~5 km²" : "~36 km²") + " hexagon";
const hexRing = id => { const ring = h3.cellToBoundary(id, true); ring.push(ring[0]); return ring; };
const emptyCollection = () => ({ type: "FeatureCollection", features: [] });
const polygon = ring => ({ type: "Feature", geometry: { type: "Polygon", coordinates: [ring] }, properties: {} });

// MapLibre "step" expression: colour classes from break values.
function stepExpression(property, breaks, colors) {
  const expr = ["step", ["get", property], colors[0]];
  breaks.forEach((b, i) => expr.push(b, colors[i + 1]));
  return expr;
}
function legendHtml(colors, labelOf) {
  return colors.map((c, i) => `<div><i style="background:${c}"></i>${labelOf(i)}</div>`).join("");
}
function rangeLabels(breaks, fmt) {
  return i => i === 0 ? `< ${fmt(breaks[0])}`
    : i === breaks.length ? `≥ ${fmt(breaks[breaks.length - 1])}`
    : `${fmt(breaks[i - 1])} – ${fmt(breaks[i])}`;
}

// ===================================================================================================
// State
// ===================================================================================================
let measure = "value", occupancy = "all";   // exposure view
let zoomLevel = 6;                          // current hexagon resolution
let mode = "exposure", result = null;       // "exposure" | "scenario", last scenario result from the backend

const MEASURES = {
  value: { label: "Replacement value", fmt: v => fmtUSD(v * 1e6), keys: { all: "value", res: "vres", com: "vcom", ind: "vind" } },
  bldg:  { label: "Buildings", fmt: fmtN, keys: { all: "bldg", res: "bres", com: "bcom", ind: "bind" } },
  occ:   { label: "Residents", fmt: fmtN, keys: { all: "occ", res: "occ", com: "occ", ind: "occ" } },
};
const OCCUPANCIES = { all: "All", res: "Residential", com: "Commercial", ind: "Industrial" };
const OCCUPANCY_NOTES = {
  all: "",
  res: "Residential: Zensus 2022 building counts per 1 km cell, scaled to GEM state totals.",
  com: "Commercial: GEM state totals spread by population (weighted towards cities). A proxy, not observed locations.",
  ind: "Industrial: GEM state totals spread by the industrial land of CORINE Land Cover (CLC5 2018), plus a small population baseline. Not observed building locations.",
};
const metricKey = () => MEASURES[measure].keys[occupancy];

// ===================================================================================================
// Exposure view
// ===================================================================================================
function renderTotals() {
  const t = DATA.totals;
  $("kpis").innerHTML =
    `<div class="kpi"><b>${fmtUSD(t.value)}</b><span>replacement value</span></div>` +
    `<div class="kpi"><b>${fmtN(t.bldg)}</b><span>buildings</span></div>` +
    `<div class="kpi"><b>${fmtN(t.occ)}</b><span>residents</span></div>`;
}

function renderStateTable() {
  const key = metricKey();
  const rows = DATA.states.slice().sort((a, b) => b[key] - a[key]);
  const max = rows[0][key];
  $("states").innerHTML = rows.map(s =>
    `<tr class="row" data-nuts="${s.nuts}"><td class="n">${s.name}</td>` +
    `<td class="b"><div class="bar" style="width:${100 * s[key] / max}%"></div></td>` +
    `<td class="v">${measure === "value" ? fmtUSD(s[key]) : fmtN(s[key])}</td></tr>`).join("");
  $("states").querySelectorAll("tr").forEach(tr => tr.onclick = () => zoomToState(tr.dataset.nuts));
}

function zoomToState(nuts) {
  const feature = DATA.outline.features.find(f => f.properties.NUTS_ID === nuts);
  if (!feature) return;
  let [west, south, east, north] = [180, 90, -180, -90];
  (function walk(c) {
    if (typeof c[0] === "number") { west = Math.min(west, c[0]); south = Math.min(south, c[1]); east = Math.max(east, c[0]); north = Math.max(north, c[1]); }
    else c.forEach(walk);
  })(feature.geometry.coordinates);
  map.fitBounds([[west, south], [east, north]], { padding: 40, duration: 700 });
}

function renderMeasureControls() {
  $("metrics").innerHTML = Object.entries(MEASURES).map(([k, m]) =>
    `<label><input type="radio" name="m" value="${k}" ${k === measure ? "checked" : ""}> ${m.label}</label>`).join("");
  $("metrics").querySelectorAll("input").forEach(i => i.onchange = () => { measure = i.value; renderOccupancyControls(); applyExposureMetric(); });
  renderOccupancyControls();
}
function renderOccupancyControls() {
  const seg = $("occ");
  const disabled = k => measure === "occ" && k !== "res" && k !== "all";
  seg.innerHTML = Object.entries(OCCUPANCIES).map(([k, label]) =>
    `<button data-o="${k}" aria-pressed="${k === occupancy}" ${disabled(k) ? "disabled" : ""}>${label}</button>`).join("");
  seg.querySelectorAll("button").forEach(b => b.onclick = () => { occupancy = b.dataset.o; renderOccupancyControls(); applyExposureMetric(); });
  $("note").textContent = measure === "occ" ? "Residents are counted for residential buildings only." : OCCUPANCY_NOTES[occupancy];
}

const exposureColors = (res) => stepExpression(metricKey(), DATA["r" + res].breaks[metricKey()], ramp(BLUE_RAMP));
function renderExposureLegend() {
  const breaks = DATA["r" + zoomLevel].breaks[metricKey()];
  const what = MEASURES[measure].label + (measure === "occ" ? "" : " · " + OCCUPANCIES[occupancy].toLowerCase());
  $("legend-title").textContent = what + " per " + (zoomLevel === 6 ? "~36 km²" : "~5 km²") + " hexagon";
  $("legend").innerHTML = legendHtml(ramp(BLUE_RAMP), rangeLabels(breaks, MEASURES[measure].fmt));
}
function applyExposureMetric() {
  [6, 7].forEach(r => { if (map.getLayer("hex" + r)) map.setPaintProperty("hex" + r, "fill-color", exposureColors(r)); });
  renderLegend();
  renderStateTable();
}
const renderLegend = () => (mode === "scenario" && result ? renderScenarioLegend() : renderExposureLegend());

const exposureFeaturesBuilt = {};
function buildExposureFeatures(res) {
  if (exposureFeaturesBuilt[res]) return;
  const d = DATA["r" + res];
  const properties = DATA.keys.concat(["cdn", "mur", "w", "cr"]);
  const features = d.ids.map((id, i) => ({
    type: "Feature", geometry: { type: "Polygon", coordinates: [hexRing(id)] },
    properties: Object.fromEntries([["i", i], ...properties.map(k => [k, d[k][i]])]),
  }));
  exposureFeaturesBuilt[res] = true;
  map.getSource("hex" + res).setData({ type: "FeatureCollection", features });
}

// ===================================================================================================
// Map setup
// ===================================================================================================
const map = new maplibregl.Map({
  container: "map", bounds: GERMANY_BOUNDS, fitBoundsOptions: { padding: 30 },
  style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#121211" } }] },
  attributionControl: false, dragRotate: false, minZoom: 3,
});
map.touchZoomRotate.disableRotation();
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: "GEM · Zensus 2022 · © EuroGeographics" }));

// Zooming out stops 0.6 zoom levels beyond the view that fits all of Germany, whatever the screen size.
function fitMinZoom() {
  const camera = map.cameraForBounds(GERMANY_BOUNDS, { padding: 30 });
  if (camera) map.setMinZoom(Math.max(2, camera.zoom - 0.6));
}
addEventListener("resize", fitMinZoom);

const tip = $("tip");
function paintTheme() {
  const css = getComputedStyle(document.documentElement);
  map.setPaintProperty("bg", "background-color", css.getPropertyValue("--map-bg").trim());
  if (map.getLayer("outline")) map.setPaintProperty("outline", "line-color", css.getPropertyValue("--outline").trim());
  if (map.getLayer("hex6")) applyExposureMetric();
  if (result) applyScenarioColors();
}

function addLayers() {
  const empty = () => ({ type: "geojson", data: emptyCollection() });
  map.addSource("outline", { type: "geojson", data: DATA.outline });
  [6, 7].forEach(r => { map.addSource("hex" + r, empty()); map.addSource("sc" + r, empty()); });
  ["rupture", "hover"].forEach(name => map.addSource(name, empty()));

  const zoomRange = r => (r === 6 ? { maxzoom: HEX_SPLIT_ZOOM } : { minzoom: HEX_SPLIT_ZOOM });
  [6, 7].forEach(r => map.addLayer({ id: "hex" + r, type: "fill", source: "hex" + r, ...zoomRange(r),
    paint: { "fill-color": exposureColors(r), "fill-opacity": 0.92 } }));
  [6, 7].forEach(r => map.addLayer({ id: "scen" + r, type: "fill", source: "sc" + r, ...zoomRange(r),
    layout: { visibility: "none" }, paint: { "fill-color": ORANGE, "fill-opacity": 0.9 } }));
  map.addLayer({ id: "hover", type: "line", source: "hover", paint: { "line-color": ORANGE, "line-width": 2.5 } });
  map.addLayer({ id: "outline", type: "line", source: "outline", paint: { "line-color": "#c3c2b7", "line-width": 0.8, "line-opacity": 0.7 } });
  map.addLayer({ id: "rupture-fill", type: "fill", source: "rupture", paint: { "fill-color": RED, "fill-opacity": 0.25 } });
  map.addLayer({ id: "rupture-line", type: "line", source: "rupture", paint: { "line-color": RED, "line-width": 2 } });
}

const CITIES = [["Berlin", 13.40, 52.52], ["Hamburg", 9.99, 53.55], ["München", 11.58, 48.14], ["Köln", 6.96, 50.94],
  ["Frankfurt", 8.68, 50.11], ["Stuttgart", 9.18, 48.78], ["Düsseldorf", 6.78, 51.23], ["Leipzig", 12.37, 51.34],
  ["Dresden", 13.74, 51.05], ["Hannover", 9.73, 52.37], ["Nürnberg", 11.08, 49.45], ["Bremen", 8.80, 53.08],
  ["Rostock", 12.14, 54.09], ["Kiel", 10.14, 54.32], ["Saarbrücken", 7.0, 49.24], ["Aachen", 6.08, 50.78],
  ["Freiburg", 7.85, 47.99], ["Dortmund", 7.47, 51.51], ["Erfurt", 11.03, 50.98], ["Magdeburg", 11.63, 52.12]];
function addCityLabels() {  // HTML markers: no tiles or font files needed
  CITIES.forEach(([name, lon, lat]) => {
    const el = document.createElement("div");
    el.className = "city"; el.textContent = name;
    new maplibregl.Marker({ element: el, anchor: "left", offset: [4, 0] }).setLngLat([lon, lat]).addTo(map);
  });
}

// ===================================================================================================
// Hover: one-feature highlight source (cheap to update); queries run at most once per frame
// ===================================================================================================
let hoverId = null, hoverZoom = 0, pendingMove = null;
const hoverLayer = () => (mode === "scenario" && result ? "scen" : "hex") + zoomLevel;

function setHover(feature) {
  const id = feature ? (feature.properties.h || feature.properties.i) : null;
  if (id === hoverId && zoomLevel === hoverZoom) return;
  hoverId = id; hoverZoom = zoomLevel;
  const source = map.getSource("hover");
  if (id === null) { source.setData(emptyCollection()); return; }
  source.setData(polygon(hexRing(feature.properties.h || DATA["r" + zoomLevel].ids[id])));
}

function tooltipHtml(p) {
  const row = (label, value) => `<div class="r">${label} <em>${value}</em></div>`;
  const usd = v => fmtUSD(v * 1e6);
  if (mode === "scenario" && result) {
    return `<b>${hexLabel(zoomLevel)}</b>` +
      row("Shaking", `MMI ${toRoman(p.mmi)} · PGA ${(+p.pga).toFixed(2)} g`) +
      row("Economic loss", usd(p.loss)) + row("Loss ratio", SCENARIO_LAYERS.ratio.fmt(p.ratio)) +
      row("Fatalities (expected)", fmtSmall(+p.fat)) + row("Exposed value", usd(p.value));
  }
  const split = "&nbsp;&nbsp;residential / commercial / industrial";
  return `<b>${hexLabel(zoomLevel)}</b>` +
    row("Replacement value", usd(p.value)) + row(split, `${usd(p.vres)} / ${usd(p.vcom)} / ${usd(p.vind)}`) +
    row("Buildings", fmtN(p.bldg)) + row(split, `${fmtN(p.bres)} / ${fmtN(p.bcom)} / ${fmtN(p.bind)}`) +
    row("Residents", fmtN(p.occ)) +
    `<div class="r" style="margin-top:4px">Residential stock, modelled:</div>` +
    row("&nbsp;&nbsp;without seismic design", p.cdn + " %") +
    row("&nbsp;&nbsp;masonry / timber / concrete", `${p.mur} / ${p.w} / ${p.cr} %`);
}

function onMapMove() {
  const e = pendingMove; pendingMove = null;
  if (!e) return;
  const feature = map.queryRenderedFeatures(e.point, { layers: [hoverLayer()] })[0];
  setHover(feature);
  map.getCanvas().style.cursor = feature ? "pointer" : (mode === "scenario" ? "crosshair" : "");
  if (!feature) { tip.style.display = "none"; return; }
  tip.innerHTML = tooltipHtml(feature.properties);
  tip.style.display = "block";
  tip.style.left = Math.min(e.point.x + 14, map.getContainer().clientWidth - 250) + "px";
  tip.style.top = Math.max(e.point.y - 20, 8) + "px";
}

// ===================================================================================================
// Scenario view
// ===================================================================================================
const SCENARIO_LAYERS = {
  mmi:   { label: "Shaking intensity (MMI)", breaks: { 6: [3.5, 4.5, 5.5, 6.5, 7.5, 8.5], 7: [3.5, 4.5, 5.5, 6.5, 7.5, 8.5] },
           fmt: v => "MMI " + toRoman(v), labels: ["< IV", "IV", "V", "VI", "VII", "VIII", "≥ IX"] },
  loss:  { label: "Economic loss", breaks: { 6: [0.5, 5, 25, 100, 500, 2500], 7: [0.1, 1, 5, 20, 100, 500] }, fmt: v => fmtUSD(v * 1e6) },
  ratio: { label: "Loss ratio (loss / exposed value)", breaks: { 6: [0.0005, 0.002, 0.005, 0.02, 0.05, 0.15], 7: [0.0005, 0.002, 0.005, 0.02, 0.05, 0.15] },
           fmt: v => (v * 100).toFixed(v < 0.01 ? 2 : 1) + " %" },
  fat:   { label: "Fatalities (expected, night)", breaks: { 6: [0.05, 0.5, 2.5, 10, 50, 250], 7: [0.01, 0.1, 0.5, 2, 10, 50] }, fmt: fmtSmall },
};
// [id, label, lon, lat, magnitude, depth km, strike, mechanism]
const PRESETS = [
  ["", "Example scenarios …"],
  ["albstadt", "Swabian Alb, M5.7 (inspired by 1978)", 9.00, 48.25, 5.7, 8, 10, "strike-slip"],
  ["roermond", "Roermond area, M5.3 (inspired by 1992)", 5.93, 51.17, 5.3, 17, 135, "normal"],
  ["dueren", "Düren, M6.1 (inspired by 1756)", 6.5, 50.8, 6.1, 10, 135, "normal"],
  ["cologne", "Cologne, M6.5 (fictional)", 6.96, 50.94, 6.5, 10, 135, "normal"],
  ["munich", "Munich, M6.0 (fictional)", 11.58, 48.14, 6.0, 10, 90, "normal"],
  ["berlin", "Berlin, M5.5 (fictional)", 13.40, 52.52, 5.5, 8, 0, "strike-slip"],
];
const MAGNITUDE_REALISTIC_MAX = 7;
const DAMAGE_GRADES = [
  { key: "slight", label: "Slight (structural loss 2–10 %)", color: "#f4a47c" },
  { key: "moderate", label: "Moderate (10–30 %)", color: "#eb6834" },
  { key: "extensive", label: "Extensive (30–70 %)", color: "#c24a1d" },
  { key: "complete", label: "Complete (> 70 %)", color: "#8f3512" },
];

const scenario = { epicentre: null, layer: "mmi" };
let marker = null, requestController = null, requestTimer = null;

function applyScenarioColors() {
  [6, 7].forEach(r => {
    if (map.getLayer("scen" + r)) {
      map.setPaintProperty("scen" + r, "fill-color", stepExpression(scenario.layer, SCENARIO_LAYERS[scenario.layer].breaks[r], ramp(ORANGE_RAMP)));
    }
  });
  renderLegend();
}
function renderScenarioLegend() {
  const layer = SCENARIO_LAYERS[scenario.layer], breaks = layer.breaks[zoomLevel];
  $("sc-legend-title").textContent = layer.label + (scenario.layer === "mmi" ? "" : " per " + (zoomLevel === 6 ? "~36 km²" : "~5 km²") + " hexagon");
  $("sc-legend").innerHTML = legendHtml(ramp(ORANGE_RAMP), layer.labels ? (i => layer.labels[i]) : rangeLabels(breaks, layer.fmt));
}

function setMode(next) {
  mode = next;
  $("tabExposure").setAttribute("aria-selected", next === "exposure");
  $("tabScenario").setAttribute("aria-selected", next === "scenario");
  $("tab-exposure").hidden = next !== "exposure";
  $("tab-scenario").hidden = next !== "scenario";
  const showResult = next === "scenario" && result;
  [6, 7].forEach(r => {
    map.setPaintProperty("hex" + r, "fill-opacity", showResult ? 0.28 : 0.92);  // exposure stays faintly visible
    map.setLayoutProperty("scen" + r, "visibility", showResult ? "visible" : "none");
  });
  ["rupture-fill", "rupture-line"].forEach(l => map.setLayoutProperty(l, "visibility", next === "scenario" ? "visible" : "none"));
  if (marker) marker.getElement().style.display = next === "scenario" ? "block" : "none";
  map.getCanvas().style.cursor = next === "scenario" ? "crosshair" : "";
  tip.style.display = "none";
  renderLegend();
}

const setStatus = (text, isError) => { $("status").textContent = text; $("status").classList.toggle("err", !!isError); };
const scheduleRun = () => { clearTimeout(requestTimer); requestTimer = setTimeout(runScenario, 250); };

function setEpicentre(lon, lat) {
  scenario.epicentre = [Math.min(16, Math.max(5, lon)), Math.min(55.5, Math.max(46.5, lat))];
  if (!marker) {
    const el = document.createElement("div");
    el.className = "epi";
    marker = new maplibregl.Marker({ element: el, draggable: true }).setLngLat(scenario.epicentre).addTo(map);
    marker.on("dragend", () => { const p = marker.getLngLat(); setEpicentre(p.lng, p.lat); });
  } else marker.setLngLat(scenario.epicentre);
  $("hint").textContent = `Epicentre: ${scenario.epicentre[1].toFixed(2)}° N, ${scenario.epicentre[0].toFixed(2)}° E. Drag the marker or click elsewhere to move it.`;
  scheduleRun();
}

async function runScenario() {
  if (!scenario.epicentre) return;
  if (requestController) requestController.abort();
  requestController = new AbortController();
  setStatus("Calculating …");
  const body = { lon: scenario.epicentre[0], lat: scenario.epicentre[1], mag: +$("mag").value, depth: +$("depth").value,
                 strike: +$("strike").value, mechanism: $("mech").value, gmpe: $("gmpe").value };
  try {
    const response = await fetch(API + "/api/scenario", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: requestController.signal });
    if (!response.ok) throw new Error("Server answered " + response.status);
    result = await response.json();
    renderResult();
    setStatus(`Calculated in ${result.seconds} s · ${fmtN(result.n_cells_affected)} grid cells affected`);
  } catch (e) {
    if (e.name === "AbortError") return;
    setStatus(e instanceof TypeError ? "Backend not reachable. Start it with: quakerisk serve" : String(e.message || e), true);
  }
}

function resultFeatures(res) {
  const d = result.layers["r" + res];
  return { type: "FeatureCollection", features: d.ids.map((id, i) => ({
    type: "Feature", geometry: { type: "Polygon", coordinates: [hexRing(id)] },
    properties: { h: id, mmi: d.mmi[i], pga: d.pga[i], loss: d.loss[i], fat: d.fat[i], value: d.value[i],
                  ratio: d.value[i] > 0 ? d.loss[i] / d.value[i] : 0 },
  })) };
}

const kpi = (value, label, detail) => `<div class="kpi"><b>${value}</b><span>${label}</span><small>${detail}</small></div>`;
function stackedBar(items, fmt) {
  const total = items.reduce((a, b) => a + b.value, 0) || 1;
  return `<div class="stack">${items.map(i => `<i style="width:${100 * i.value / total}%;background:${i.color}" title="${i.label}: ${fmt(i.value)}"></i>`).join("")}</div>` +
    items.map(i => `<div class="lg"><span><i style="background:${i.color}"></i>${i.label}</span><em>${fmt(i.value)} · ${(100 * i.value / total).toFixed(0)} %</em></div>`).join("");
}

function renderResult() {
  [6, 7].forEach(r => map.getSource("sc" + r).setData(resultFeatures(r)));
  map.getSource("rupture").setData(polygon([...result.scenario.rupture, result.scenario.rupture[0]]));
  $("results").hidden = false;
  setMode("scenario");
  applyScenarioColors();

  const R = result, grades = R.buildings_by_grade;
  $("kpis2").innerHTML =
    kpi(fmtUSD(R.loss_usd), "Economic loss", `${(R.loss_ratio * 100).toFixed(2)} % of value in shaken area`) +
    kpi(fmtSmall(R.fatalities), "Fatalities", "expected, night-time, residents") +
    kpi(fmtN(grades.extensive + grades.complete), "Heavily damaged buildings", "extensive or complete damage") +
    kpi(fmtN(R.people_exposed_mmi["MMI>=6"]), "People at MMI VI or more", `peak: ${R.max_pga_g.toFixed(2)} g · MMI ${toRoman(R.max_mmi)}`);
  $("ch-grades").innerHTML = stackedBar(DAMAGE_GRADES.map(g => ({ label: g.label, value: grades[g.key], color: g.color })), fmtN) +
    `<div class="note flush">Share of a building's structural value lost, derived from the vulnerability model (no fragility curves are available).</div>`;
  const c = R.loss_by_component_usd;
  $("ch-comp").innerHTML = stackedBar([{ label: "Structural", value: c.structural, color: "var(--c1)" },
    { label: "Non-structural", value: c.nonstructural, color: "var(--c3)" }, { label: "Contents", value: c.contents, color: "var(--c4)" }], fmtUSD);
  const o = R.loss_by_occupancy_usd;
  $("ch-occ").innerHTML = stackedBar([{ label: "Residential", value: o.res, color: "var(--c1)" },
    { label: "Commercial", value: o.com, color: "var(--c3)" }, { label: "Industrial", value: o.ind, color: "var(--c4)" }], fmtUSD);
  const names = Object.fromEntries(DATA.states.map(s => [s.state, s.name]));
  const top = R.by_state.filter(s => s.loss > 0).sort((a, b) => b.loss - a.loss).slice(0, 6);
  const max = top.length ? top[0].loss : 1;
  $("ch-states").innerHTML = top.map(s => `<tr><td class="n">${names[s.state]}</td><td class="b"><div class="bar" style="width:${100 * s.loss / max}%"></div></td><td class="v">${fmtUSD(s.loss)}</td></tr>`).join("");
}

function initScenarioControls() {
  $("preset").innerHTML = PRESETS.map(p => `<option value="${p[0]}">${p[1]}</option>`).join("");
  $("scmetrics").innerHTML = Object.entries(SCENARIO_LAYERS).map(([k, l]) =>
    `<label><input type="radio" name="scm" value="${k}" ${k === scenario.layer ? "checked" : ""}> ${l.label}</label>`).join("");
  $("scmetrics").querySelectorAll("input").forEach(i => i.onchange = () => { scenario.layer = i.value; applyScenarioColors(); });

  const outputs = { mag: v => (+v).toFixed(1) + (+v > MAGNITUDE_REALISTIC_MAX ? " ⚠" : ""), depth: v => v + " km", strike: v => v + "°" };
  const refreshOutputs = () => {
    Object.entries(outputs).forEach(([k, fmt]) => { $("o-" + k).textContent = fmt($(k).value); });
    $("magwarn").hidden = +$("mag").value <= MAGNITUDE_REALISTIC_MAX;
  };
  Object.keys(outputs).forEach(k => {
    $(k).oninput = refreshOutputs;
    $(k).onchange = () => { if (scenario.epicentre) scheduleRun(); };
  });
  $("mech").onchange = $("gmpe").onchange = () => { if (scenario.epicentre) scheduleRun(); };
  $("preset").onchange = () => {
    const p = PRESETS.find(x => x[0] === $("preset").value);
    if (!p || !p[0]) return;
    $("mag").value = p[4]; $("depth").value = p[5]; $("strike").value = p[6]; $("mech").value = p[7];
    refreshOutputs();
    setEpicentre(p[2], p[3]);
    map.flyTo({ center: [p[2], p[3]], zoom: Math.max(map.getZoom(), 7), duration: 800 });
  };
  $("tabExposure").onclick = () => setMode("exposure");
  $("tabScenario").onclick = () => setMode("scenario");
  map.on("click", e => { if (mode === "scenario") setEpicentre(e.lngLat.lng, e.lngLat.lat); });
}

// ===================================================================================================
// Start-up
// ===================================================================================================
$("theme").onclick = () => {
  document.documentElement.dataset.theme = isDark() ? "light" : "dark";
  try { localStorage.setItem("theme", document.documentElement.dataset.theme); } catch (e) { /* storage unavailable */ }
  paintTheme();
};

map.on("load", () => {
  addLayers();
  addCityLabels();
  buildExposureFeatures(6);
  setTimeout(() => buildExposureFeatures(7), 200);  // finer hexagons are built right after the first paint
  fitMinZoom(); setTimeout(fitMinZoom, 500); setTimeout(fitMinZoom, 2000);
  paintTheme();
  zoomLevel = map.getZoom() >= HEX_SPLIT_ZOOM ? 7 : 6;
  renderLegend();
  map.on("zoom", () => {
    const level = map.getZoom() >= HEX_SPLIT_ZOOM ? 7 : 6;
    if (level !== zoomLevel) { zoomLevel = level; renderLegend(); }
  });
  map.on("mousemove", e => { if (!pendingMove) requestAnimationFrame(onMapMove); pendingMove = e; });
  map.on("mouseout", () => { pendingMove = null; tip.style.display = "none"; setHover(null); });
  initScenarioControls();
});

renderTotals();
renderMeasureControls();
renderStateTable();
