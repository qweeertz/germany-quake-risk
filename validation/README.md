# Validation against the OpenQuake engine

The loss engine (`src/quakerisk/engine.py`) was compared with the OpenQuake engine 3.26.2 (`scenario_risk`) on identical
inputs:

- the same planar rupture (exported from hazardlib),
- the same 1.3-1.8 million exposure assets (cell x vulnerability function, merged over residential, commercial and
  industrial buildings),
- the same GEM vulnerability functions (mean loss ratios, `ignore_covs = true`),
- one GMPE (Akkar et al. 2014), Vs30 = 400 m/s, truncation level 3,
- 500 ground-motion fields sampled by OpenQuake (inter- plus intra-event variability, no spatial correlation). The loss
  engine integrates the same variability analytically (21-point Gauss-Legendre quadrature on the normal truncated at
  +-3 sigma), so expected values must agree up to Monte-Carlo error.

## Results

| Scenario | OpenQuake economic loss | Loss engine | Ratio | OpenQuake Monte-Carlo error | Fatalities ratio |
|---|---|---|---|---|---|
| Düren, M5.8, 10 km, normal fault | 3.58 bn USD | 3.68 bn USD | 1.027 | 2.2 % | 1.024 |
| Munich, M5.5, 8 km, normal fault | 7.23 bn USD | 7.37 bn USD | 1.020 | 2.6 % | 1.009 |

- Both differences are within about one Monte-Carlo standard error. By component (structural, non-structural,
  contents) and by distance band (0-10 to 70-120 km) the ratios stay between 0.99 and 1.08; per 1 km cell the losses lie
  on the 1:1 line (`<case>/comparison.png`).
- Median ground motion agrees to within 4 % (PGA 0-1 %, SA(0.3) 3-4 %). The sampled sigma is about 2 % lower than the one
  used by the loss engine, consistent with OpenQuake truncating inter- and intra-event variability separately. This
  probably explains the small remaining +2 % bias.
- **Not covered:** the GMPE ensemble used by default (a log-space average of four models, an engineering choice), the
  damage grades (OpenQuake has no fragility curves here), and the realism of the vulnerability model itself (see the
  limitations in the main README).
- The validation found an error in the first version of the quadrature (variability clipped instead of truncated at
  3 sigma, about 6 % too high); it was corrected before the figures above.

## Reproduce

OpenQuake needs the package versions pinned in `pyproject.toml` (in particular pandas 2.2 and numba 0.61); it runs without
GDAL. Install the optional plotting dependency first: `pip install -e ".[validation]"`.

```bash
python validation/run_validation.py --name duren_m58 --events 500
python validation/compare.py duren_m58

python validation/run_validation.py --name munich_m55 --events 500 --lon 11.58 --lat 48.14 --mag 5.5 --depth 8 --strike 90
python validation/compare.py munich_m55
```

Each run writes its inputs and results to `validation/<name>/` (`comparison.json` and `comparison.png` are tracked,
the large input files are not). The OpenQuake datastores are kept in `~/oqdata/calc_<id>.hdf5`; the id is stored in
`<name>/meta.json`. `run_validation.py --skip-openquake` re-runs only the loss engine for an existing case, for example
after a code change.
