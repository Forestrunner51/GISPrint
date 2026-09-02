"""Local equirectangular projection: WGS84 degrees -> metres about a bbox centre."""
import math


class LocalPlane:
    def __init__(self, bbox):
        s, w, n, e = bbox
        self.lat0 = (s + n) / 2.0
        self.lon0 = (w + e) / 2.0
        self.mx = 111320.0 * math.cos(math.radians(self.lat0))
        self.my = 110540.0

    def xy(self, lon, lat):
        return ((lon - self.lon0) * self.mx, (lat - self.lat0) * self.my)

    def span_m(self, bbox):
        s, w, n, e = bbox
        return ((e - w) * self.mx, (n - s) * self.my)
