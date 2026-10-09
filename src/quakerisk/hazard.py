"""Ground motion of a scenario earthquake: rupture geometry, GMPEs and shaking intensity.

The rupture is a planar surface (OpenQuake hazardlib, Wells & Coppersmith scaling). Ground motion is the median and
standard deviation (natural log of g) of one or several GMPEs at every 1 km cell.
"""
import numpy as np
from openquake.hazardlib.contexts import simple_cmaker
from openquake.hazardlib.geo import Mesh, Point
from openquake.hazardlib.geo.surface import PlanarSurface
from openquake.hazardlib.gsim.base import registry
from openquake.hazardlib.scalerel import WC1994
from pyproj import Transformer

IMTS = ["PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)"]  # the intensity measures used by the vulnerability functions
VS30_DEFAULT = 400.0  # m/s, uniform site condition
MAX_RUPTURE_LENGTH_KM = 1000.0
MAX_GMPE_MAGNITUDE = 8.0  # the GMPEs are not valid beyond this; larger events use the M8 motion with the real rupture
CRUST_BOTTOM_KM = 30.0

# Default: equal-weight ensemble of established European shallow-crustal GMPEs. The ESHM20 backbone (Kotha et al. 2020,
# mid stress) predicts about 1.7-2.8 times higher motion for moderate German events; it is offered as a high-motion
# alternative. "akkar" is a single model, used to validate the loss calculation against the OpenQuake engine.
GMPE_SETS = {
    "ensemble": [("AkkarEtAlRjb2014", 0.25), ("BindiEtAl2014Rjb", 0.25), ("CauzziEtAl2014", 0.25),
                 ("LanzanoEtAl2019_RJB_OMO", 0.25)],
    "eshm20": [("KothaEtAl2020ESHM20", 1.0)],
    "akkar": [("AkkarEtAlRjb2014", 1.0)],
}


def build_rupture(lon, lat, mag, depth, strike, dip, rake):
    """Planar rupture centred on the hypocentre; returns the surface and its length in km.

    The area follows Wells & Coppersmith. The width is limited to the crust (top edge at the surface at the shallowest,
    bottom edge above 30 km) and the length to MAX_RUPTURE_LENGTH_KM, so that very large magnitudes remain computable.
    """
    area = WC1994().get_median_area(mag, rake)
    max_width = 0.98 * 2 * max(min(depth, CRUST_BOTTOM_KM - depth), 1.0) / np.sin(np.radians(dip))
    width = min(np.sqrt(area), max_width)
    length = min(area / width, MAX_RUPTURE_LENGTH_KM)
    capped_area = length * width
    msr = WC1994()
    msr.get_median_area = lambda mag_, rake_: capped_area  # area after the width and length limits
    surface = PlanarSurface.from_hypocenter(Point(lon, lat, depth), msr, mag, length / width, strike, dip, rake)
    return surface, length


def mmi_from_pga(pga_g):
    """Modified Mercalli intensity from PGA (Worden et al. 2012); roughly equal to EMS-98."""
    y = np.log10(np.maximum(pga_g * 981.0, 1e-3))  # cm/s2
    return np.clip(np.where(y <= 1.57, 1.78 + 1.55 * y, -1.60 + 3.70 * y), 1, 10)


class GroundMotionModel:
    """Evaluates the ground motion at the cell centres for a scenario rupture."""

    def __init__(self, lon, lat, x, y):
        self.lon, self.lat, self.x, self.y = lon, lat, x, y  # cell centres in WGS84 and in EPSG:3035
        self.to_3035 = Transformer.from_crs(4326, 3035, always_xy=True)
        self.gmpes = {name: (simple_cmaker([registry[g]() for g, _ in members], IMTS),
                             np.array([w for _, w in members])) for name, members in GMPE_SETS.items()}

    def evaluate(self, lon, lat, mag, depth, strike, dip, rake, vs30=VS30_DEFAULT, gmpe="ensemble", radius_km=300):
        """-> (cell indices, Rjb, mean ln PGA/SA per cell, sigma, rupture surface); mean and sigma are (imts, cells)."""
        surface, length = build_rupture(lon, lat, mag, depth, strike, dip, rake)
        epi_x, epi_y = self.to_3035.transform(lon, lat)
        radius_km = max(radius_km, 300 + length / 2)
        near = np.where(np.hypot(self.x - epi_x, self.y - epi_y) < radius_km * 1000)[0]
        mesh = Mesh(self.lon[near], self.lat[near])
        hypocenter = Point(lon, lat, depth)
        n = len(near)

        maker, weights = self.gmpes[gmpe]
        ctx = maker.new_ctx(n)
        ctx.mag, ctx.hypo_depth, ctx.rake, ctx.dip = min(mag, MAX_GMPE_MAGNITUDE), depth, rake, dip
        ctx.rjb = surface.get_joyner_boore_distance(mesh)
        ctx.rrup = surface.get_min_distance(mesh)
        ctx.rx = surface.get_rx_distance(mesh)
        ctx.ry0 = surface.get_ry0_distance(mesh)
        ctx.repi = Point(lon, lat).distance_to_mesh(mesh, with_depths=False)
        ctx.rhypo = hypocenter.distance_to_mesh(mesh, with_depths=True)
        ctx.ztor, ctx.width = surface.get_top_edge_depth(), surface.get_width()
        ctx.vs30, ctx.vs30measured = np.full(n, vs30), np.zeros(n, bool)
        ctx.z1pt0, ctx.z2pt5, ctx.region = np.full(n, 0.1), np.full(n, 1.0), np.zeros(n, int)

        mean, sigma, _, _ = maker.get_mean_stds([ctx])  # each (gmpes, imts, cells)
        combined_mean = np.tensordot(weights, mean, 1)
        if len(weights) == 1:
            combined_sigma = sigma[0]
        else:  # within-model variance plus the spread between the models
            combined_sigma = np.sqrt(np.tensordot(weights, sigma ** 2, 1)
                                     + np.tensordot(weights, (mean - combined_mean) ** 2, 1))
        return near, ctx.rjb, combined_mean, combined_sigma, surface
