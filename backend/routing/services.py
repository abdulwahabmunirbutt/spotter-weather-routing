"""
Third-party integrations — all free, no API keys required.

- Routing:   OSRM public demo server. Its alternatives are capped at 2
             (some corridors return only 1), so fetch_routes() tops up to
             3 options with via-point routes through offset corridor
             points — still real OSRM-computed routes, deduplicated by
             corridor overlap (see services.fetch_routes).
- Weather:   Open-Meteo forecast API (hourly wind/precip/snow, imperial units)
- Geocoding: Nominatim (OpenStreetMap)
"""
import math
from datetime import datetime, timedelta, timezone

import requests
from django.conf import settings

M_PER_MILE = 1609.344

HEADERS = {"User-Agent": settings.NOMINATIM_UA}


# ---------------------------------------------------------------- geocoding
def geocode(query: str, limit: int = 5) -> list[dict]:
    """Free-text place search -> [{label, lat, lon}]."""
    resp = requests.get(
        settings.NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": limit, "addressdetails": 0},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return [
        {"label": r["display_name"], "lat": float(r["lat"]), "lon": float(r["lon"])}
        for r in resp.json()
    ]


# ---------------------------------------------------------------- routing
def _osrm_route(coords: str, alternatives: str = "0") -> list[dict]:
    """One OSRM call -> normalized route dicts (may be fewer than asked)."""
    url = f"{settings.OSRM_BASE_URL}/route/v1/driving/{coords}"
    resp = requests.get(
        url,
        params={
            "alternatives": alternatives,
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM error: {data.get('code')}")
    return [
        {
            "distance_mi": round(r["distance"] / M_PER_MILE, 1),
            "duration_sec": int(r["duration"]),
            # GeoJSON is [lon, lat]; Leaflet wants [lat, lon]
            "geometry": [[c[1], c[0]] for c in r["geometry"]["coordinates"]],
        }
        for r in data["routes"]
    ]


def _point_at_fraction(geometry: list[list[float]], frac: float):
    """Point `frac` of the way along the polyline (+ local direction)."""
    total = sum(
        haversine_miles(tuple(geometry[i]), tuple(geometry[i + 1]))
        for i in range(len(geometry) - 1)
    )
    target = total * frac
    acc = 0.0
    for i in range(len(geometry) - 1):
        a, b = geometry[i], geometry[i + 1]
        seg = haversine_miles(tuple(a), tuple(b))
        if acc + seg >= target and seg > 0:
            t = (target - acc) / seg
            point = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
            return point, (b[0] - a[0], b[1] - a[1]), total
        acc += seg
    last = geometry[-1]
    prev = geometry[-2] if len(geometry) > 1 else geometry[0]
    return list(last), (last[0] - prev[0], last[1] - prev[1]), total


def _offset_point(point, direction, miles: float, side: int):
    """Move `point` perpendicular to `direction` by `miles` (side = ±1)."""
    lat, lon = point
    # Local tangent-plane conversion (degrees per mile).
    dlat_mi = 69.0
    dlon_mi = 69.0 * max(math.cos(math.radians(lat)), 0.2)
    dx = direction[1] * dlon_mi  # east, in miles
    dy = direction[0] * dlat_mi  # north, in miles
    norm = math.hypot(dx, dy) or 1.0
    # Perpendicular unit vector.
    px, py = (-dy / norm) * side, (dx / norm) * side
    return [lat + (py * miles) / dlat_mi, lon + (px * miles) / dlon_mi]


def _distinct_enough(candidate: dict, existing: list[dict]) -> bool:
    """
    A synthetic alternative only counts if it is a genuinely different
    corridor: either its length differs by >2% from every existing route,
    or >=20% of its sampled points sit >3 miles from an existing route.
    Also reject absurd detours (>35% longer than the shortest option).
    """
    shortest = min(r["distance_mi"] for r in existing)
    if candidate["distance_mi"] > 1.35 * shortest:
        return False
    for other in existing:
        if abs(candidate["distance_mi"] - other["distance_mi"]) > 0.02 * other["distance_mi"]:
            continue
        sample = candidate["geometry"][::25] or candidate["geometry"]
        far = 0
        for p in sample:
            nearest = min(
                haversine_miles(tuple(p), tuple(q))
                for q in other["geometry"][::10]
            )
            if nearest > 3.0:
                far += 1
        if far / len(sample) < 0.20:
            return False
    return True


def fetch_routes(origin: dict, destination: dict) -> list[dict]:
    """
    Three route options for the trip.

    OSRM's alternatives are capped (the public server returns at most 2,
    and some corridors only 1), so when fewer than 3 come back we top up
    with via-point routes: route through a point offset perpendicular
    from an existing route's corridor, which produces a real, distinct
    third option computed by the same routing engine.
    """
    coords = f"{origin['lon']},{origin['lat']};{destination['lon']},{destination['lat']}"
    routes = _osrm_route(coords, alternatives="3")[:3]

    attempts = [(0.5, 1), (0.5, -1), (0.35, 1), (0.65, -1), (0.35, -1), (0.65, 1)]
    for frac, side in attempts:
        if len(routes) >= 3:
            break
        base = routes[0]
        point, direction, total = _point_at_fraction(base["geometry"], frac)
        offset_mi = min(max(0.08 * total, 12.0), 60.0)
        via = _offset_point(point, direction, offset_mi, side)
        via_coords = (
            f"{origin['lon']},{origin['lat']};"
            f"{via[1]:.5f},{via[0]:.5f};"
            f"{destination['lon']},{destination['lat']}"
        )
        try:
            extra = _osrm_route(via_coords)
        except (requests.RequestException, RuntimeError):
            continue
        if extra and _distinct_enough(extra[0], routes):
            routes.append(extra[0])

    for i, r in enumerate(routes):
        r["id"] = f"route-{i + 1}"
    return routes


# ---------------------------------------------------------------- geometry
def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in miles between two (lat, lon) points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def sample_checkpoints(
    geometry: list[list[float]],
    duration_sec: int,
    departure: datetime,
    every_miles: float,
) -> list[dict]:
    """
    Walk the polyline and drop a checkpoint every `every_miles` miles.
    ETA at each checkpoint = departure + elapsed driving time, where driving
    time is distributed proportionally to segment length (OSRM gives only
    the route total, which is the standard simplification for an assessment).
    """
    checkpoints = [{"lat": geometry[0][0], "lon": geometry[0][1], "eta": departure,
                    "mile": 0.0}]
    total_dist = sum(
        haversine_miles(tuple(geometry[i]), tuple(geometry[i + 1]))
        for i in range(len(geometry) - 1)
    ) or 1
    sec_per_mile = duration_sec / total_dist

    traveled = 0.0
    next_at = every_miles
    for i in range(len(geometry) - 1):
        a, b = geometry[i], geometry[i + 1]
        seg = haversine_miles(tuple(a), tuple(b))
        while traveled + seg >= next_at and next_at < total_dist:
            frac = (next_at - traveled) / seg if seg else 0
            lat = a[0] + (b[0] - a[0]) * frac
            lon = a[1] + (b[1] - a[1]) * frac
            eta = departure + timedelta(seconds=next_at * sec_per_mile)
            checkpoints.append({"lat": lat, "lon": lon, "eta": eta, "mile": round(next_at, 1)})
            next_at += every_miles
        traveled += seg

    # Always include the destination as the final checkpoint.
    last = geometry[-1]
    checkpoints.append(
        {
            "lat": last[0],
            "lon": last[1],
            "eta": departure + timedelta(seconds=duration_sec),
            "mile": round(total_dist, 1),
        }
    )
    return checkpoints


# ---------------------------------------------------------------- weather
def fetch_weather_batch(points: list[dict]) -> list[dict]:
    """
    Open-Meteo hourly forecast for each checkpoint at its ETA hour.
    `points`: [{lat, lon, eta(datetime)}] -> weather dicts aligned by index.
    Open-Meteo accepts arrays of lat/lon, so we batch in one call.
    """
    if not points:
        return []

    lats = ",".join(f"{p['lat']:.4f}" for p in points)
    lons = ",".join(f"{p['lon']:.4f}" for p in points)
    start = min(p["eta"] for p in points).date().isoformat()
    end = max(p["eta"] for p in points).date().isoformat()

    resp = requests.get(
        settings.OPEN_METEO_URL,
        params={
            "latitude": lats,
            "longitude": lons,
            "hourly": "wind_speed_10m,precipitation,snowfall",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "UTC",
            "start_date": start,
            "end_date": end,
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    results = payload if isinstance(payload, list) else [payload]

    out = []
    for point, entry in zip(points, results):
        hourly = entry.get("hourly", {})
        times = hourly.get("time", [])
        # Nearest hour to the truck's ETA at this checkpoint.
        eta_naive = point["eta"].astimezone(timezone.utc).replace(tzinfo=None)
        best = min(
            range(len(times)),
            key=lambda i: abs(
                datetime.fromisoformat(times[i]) - eta_naive
            ),
            default=None,
        )
        if best is None:
            out.append({"wind_mph": 0.0, "precip_in": 0.0, "snow_in": 0.0})
            continue
        out.append(
            {
                "wind_mph": float(hourly["wind_speed_10m"][best] or 0),
                "precip_in": float(hourly["precipitation"][best] or 0),
                "snow_in": float(hourly["snowfall"][best] or 0),
            }
        )
    return out
