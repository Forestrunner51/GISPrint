"""Stage 1b: elevation. Terrarium RGB tiles (AWS open data, keyless, global).

Beats SRTM for city work: z15 is ~3.8 m/px at mid latitudes against SRTM's 30 m.
elevation = R*256 + G + B/256 - 32768  (metres)
"""
import io, math, os, hashlib
import numpy as np
import requests
from PIL import Image

URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
CACHE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache", "dem")
TS = 256


def _tile_xy(lat, lon, z):
    n = 2.0 ** z
    lr = math.radians(lat)
    return ((lon + 180.0) / 360.0 * n,
            (1.0 - math.log(math.tan(lr) + 1.0 / math.cos(lr)) / math.pi) / 2.0 * n)


def _fetch(z, x, y):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{z}_{x}_{y}.png")
    if not os.path.exists(path):
        r = requests.get(URL.format(z=z, x=x, y=y), timeout=60)
        if r.status_code != 200:
            return None
        with open(path, "wb") as fh:
            fh.write(r.content)
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.float64)
    return a[:, :, 0] * 256.0 + a[:, :, 1] + a[:, :, 2] / 256.0 - 32768.0


class DEM:
    """Stitched elevation raster over a bbox, sampled bilinearly in lon/lat."""

    def __init__(self, bbox, zoom=15, smooth=1.0):
        s, w, n, e = bbox
        self.z = zoom
        x0, y0 = _tile_xy(n, w, zoom)          # north-west
        x1, y1 = _tile_xy(s, e, zoom)          # south-east
        self.tx0, self.ty0 = int(math.floor(x0)), int(math.floor(y0))
        tx1, ty1 = int(math.floor(x1)), int(math.floor(y1))
        nx, ny = tx1 - self.tx0 + 1, ty1 - self.ty0 + 1
        grid = np.zeros((ny * TS, nx * TS))
        got = 0
        for j in range(ny):
            for i in range(nx):
                t = _fetch(zoom, self.tx0 + i, self.ty0 + j)
                if t is not None:
                    grid[j * TS:(j + 1) * TS, i * TS:(i + 1) * TS] = t
                    got += 1
        if not got:
            raise RuntimeError("no DEM tiles fetched")
        if smooth > 0:
            # Terrarium quantises to 1/256 m and the source postings are coarser
            # than our sample spacing, so raw bilinear leaves visible triangle
            # facets on steep faces. A sub-posting blur removes them without
            # touching real relief.
            from scipy.ndimage import gaussian_filter
            grid = gaussian_filter(grid, sigma=smooth, mode="nearest")
        self.grid = grid
        self.tiles = (nx, ny, got)
        self.res_m = 156543.03 * math.cos(math.radians((s + n) / 2)) / (2 ** zoom)

    def sample(self, lon, lat):
        """Bilinear elevation in metres. lon/lat may be arrays."""
        lon = np.atleast_1d(np.asarray(lon, dtype=float))
        lat = np.atleast_1d(np.asarray(lat, dtype=float))
        n = 2.0 ** self.z
        lr = np.radians(lat)
        gx = ((lon + 180.0) / 360.0 * n - self.tx0) * TS
        gy = ((1.0 - np.log(np.tan(lr) + 1.0 / np.cos(lr)) / np.pi) / 2.0 * n - self.ty0) * TS
        h, w = self.grid.shape
        gx = np.clip(gx, 0, w - 1.001)
        gy = np.clip(gy, 0, h - 1.001)
        x0, y0 = np.floor(gx).astype(int), np.floor(gy).astype(int)
        fx, fy = gx - x0, gy - y0
        g = self.grid
        return ((g[y0, x0] * (1 - fx) + g[y0, x0 + 1] * fx) * (1 - fy) +
                (g[y0 + 1, x0] * (1 - fx) + g[y0 + 1, x0 + 1] * fx) * fy)
