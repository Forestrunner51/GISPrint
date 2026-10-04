"""Stage 1: OSM vector extraction via Overpass, with on-disk cache."""
import hashlib, json, math, os, re, time
import requests

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HEADERS = {"User-Agent": "tilemaker/0.1 (3d-printed map tiles)"}
CACHE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")

# Fallback storey heights (m) when a building carries no height/levels tag.
DEFAULT_LEVELS = {
    "house": 2, "detached": 2, "residential": 3, "apartments": 5,
    "retail": 1, "commercial": 4, "office": 6, "industrial": 1,
    "warehouse": 1, "church": 3, "school": 3, "garage": 1, "shed": 1,
}
METRES_PER_LEVEL = 3.2


def _cached(query: str) -> dict:
    key = hashlib.sha1(query.encode()).hexdigest()[:16]
    path = os.path.join(CACHE, f"{key}.json")
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    last = None
    # Overpass throttles hard. Rotate mirrors and back off rather than failing
    # a run that has already paid for the geometry.
    for attempt in range(3):
      for url in OVERPASS:
        try:
            r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=180)
            if r.status_code == 200:
                data = r.json()
                os.makedirs(CACHE, exist_ok=True)
                with open(path, "w") as fh:
                    json.dump(data, fh)
                return data
            last = f"{url} -> HTTP {r.status_code}"
        except Exception as exc:  # network flake / mirror down
            last = f"{url} -> {type(exc).__name__}: {exc}"
        time.sleep(2 ** attempt * 3)
    raise RuntimeError(f"Overpass unavailable after 3 rounds: {last}")


def _query(bbox, want_roads, relations):
    s, w, n, e = bbox
    b = f"{s},{w},{n},{e}"
    parts = [f"way[building]({b});"]
    if relations:
        parts.append(f"relation[building]({b});")
    if want_roads:
        parts.append(f'way[highway~"^(motorway|trunk|primary|secondary|tertiary|'
                     f'residential|unclassified|living_street|pedestrian|service)$"]({b});')
    return f"[out:json][timeout:180];({''.join(parts)});out geom;"


def fetch(bbox, want_roads=True, relations=False) -> dict:
    """bbox = (south, west, north, east) in WGS84 degrees.

    `relation[building]` with `out geom` expands multipolygon members server
    side and 504s on anything past a couple of hundred metres square, so it is
    opt-in and falls back rather than failing the run. Cost of leaving it off:
    courtyard buildings mapped as multipolygons are skipped -- a small minority
    almost everywhere, but check your city before you promise a set.
    """
    if relations:
        try:
            return _cached(_query(bbox, want_roads, True))
        except RuntimeError as exc:
            print(f"  ! building relations timed out ({exc}); ways only")
    return _cached(_query(bbox, want_roads, False))


def fetch_water(bbox) -> dict:
    """Coastline, water areas and piers, in their own query and cache entry.

    Kept apart from the building query so a set built without --water costs
    nothing extra and its cache entries stay valid. Relations come back with
    full member geometry, not clipped to the bbox: clipping breaks the rings
    we need to reassemble, and a river multipolygon is far lighter than a
    building one.
    """
    s, w, n, e = bbox
    b = f"{s},{w},{n},{e}"
    q = (f"[out:json][timeout:180];("
         f"way[natural=coastline]({b});"
         f"way[natural=water]({b});relation[natural=water]({b});"
         f"way[waterway=riverbank]({b});relation[waterway=riverbank]({b});"
         f"way[man_made=pier]({b});"
         f");out geom;")
    return _cached(q)


_NUM = re.compile(r"[-+]?\d*\.?\d+")


def parse_height(tags: dict):
    """Return metres, or None. Handles '38', '38 m', \"125'\", '125 ft'."""
    raw = tags.get("height") or tags.get("building:height")
    if raw:
        m = _NUM.search(str(raw))
        if m:
            v = float(m.group())
            if "'" in raw or "ft" in raw.lower():
                v *= 0.3048
            if 0 < v < 900:
                return v
    lv = tags.get("building:levels") or tags.get("levels")
    if lv:
        m = _NUM.search(str(lv))
        if m and 0 < float(m.group()) < 200:
            return float(m.group()) * METRES_PER_LEVEL
    return None


def guess_height(tags: dict) -> float:
    h = parse_height(tags)
    if h is not None:
        return h
    kind = tags.get("building", "yes")
    return DEFAULT_LEVELS.get(kind, 3) * METRES_PER_LEVEL


ROAD_WIDTH_M = {
    "motorway": 16, "trunk": 14, "primary": 12, "secondary": 10,
    "tertiary": 9, "residential": 7, "unclassified": 6,
    "living_street": 6, "pedestrian": 4, "service": 4,
}


def road_width(tags: dict) -> float:
    lanes = tags.get("lanes")
    if lanes:
        m = _NUM.search(str(lanes))
        if m and 0 < float(m.group()) <= 12:
            return float(m.group()) * 3.4
    return ROAD_WIDTH_M.get(tags.get("highway", ""), 6)
