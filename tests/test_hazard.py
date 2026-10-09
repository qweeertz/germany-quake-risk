import numpy as np
import pytest

from quakerisk.hazard import MAX_RUPTURE_LENGTH_KM, build_rupture, mmi_from_pga


@pytest.mark.parametrize("mag", [4.5, 6.0, 7.0, 8.0])
def test_rupture_fits_into_the_crust(mag):
    surface, length = build_rupture(10.0, 51.0, mag, depth=10, strike=0, dip=60, rake=-90)
    assert surface.get_top_edge_depth() >= -1e-6
    assert surface.get_top_edge_depth() + surface.get_width() * np.sin(np.radians(60)) <= 30.0
    assert length <= MAX_RUPTURE_LENGTH_KM


def test_mmi_increases_with_pga():
    mmi = mmi_from_pga(np.array([0.01, 0.05, 0.1, 0.3, 0.6]))
    assert (np.diff(mmi) > 0).all()
    assert 6.5 < mmi_from_pga(0.2) < 8.5


def test_shaking_decays_with_distance(engine):
    near, rjb, mean, sigma, _ = engine.ground_motion.evaluate(10.0, 51.0, 6.0, 10, 0, 60, -90)
    pga = np.exp(mean[0])
    close, far = pga[rjb < 10], pga[(rjb > 80) & (rjb < 120)]
    assert close.mean() > 5 * far.mean()
    assert (sigma > 0.4).all() and (sigma < 1.2).all()
