"""
API views.

POST /api/geocode — place search (Nominatim)
POST /api/plan    — routes + ETA-based weather sampling + risk + recommendation
POST /api/weather — re-score checkpoints at shifted ETAs (forecast slider)
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .risk import RISK_COLORS, RISK_LABELS, checkpoint_risk, recommend_route, summarize
from .services import fetch_routes, fetch_weather_batch, geocode, sample_checkpoints

ALLOWED_SAMPLE_MILES = (10, 25, 50)


def upstream_error(exc: Exception) -> Response:
    """A third-party API (OSRM / Open-Meteo / Nominatim) failed — report a
    clean 502 to the client instead of leaking a Django 500 page."""
    return Response(
        {"error": f"Upstream service unavailable, please retry shortly. ({exc})"},
        status=502,
    )


def _enrich_route(route: dict, departure, sample_miles: float, load_lb: float) -> dict:
    """Sample checkpoints along one route, fetch their weather, score risk."""
    cps = sample_checkpoints(
        route["geometry"], route["duration_sec"], departure, sample_miles
    )
    weather = fetch_weather_batch(cps)
    for cp, w in zip(cps, weather):
        score_checkpoint(cp, w, load_lb)
        cp["eta_iso"] = cp.pop("eta").isoformat()
    summary = summarize(cps)
    return {
        "id": route["id"],
        "distance_mi": route["distance_mi"],
        "duration_min": round(route["duration_sec"] / 60),
        "duration_sec": route["duration_sec"],
        "geometry": route["geometry"],
        "checkpoints": cps,
        "summary": summary,
        "avg_risk": summary["avg_risk"],
    }


def score_checkpoint(cp: dict, weather: dict, load_lb: float) -> dict:
    """Attach weather + risk fields to a checkpoint dict (mutates & returns)."""
    cp.update(weather)
    cp["risk"] = checkpoint_risk(
        weather["wind_mph"], weather["precip_in"], weather["snow_in"], load_lb
    )
    cp["risk_label"] = RISK_LABELS[cp["risk"]]
    cp["color"] = RISK_COLORS[cp["risk"]]
    return cp


@api_view(["POST"])
def geocode_view(request):
    """POST {q} -> {results: [{label, lat, lon}]}"""
    query = (request.data.get("q") or "").strip()
    if not query:
        return Response({"error": "q is required"}, status=400)
    try:
        return Response({"results": geocode(query)})
    except requests.RequestException as exc:
        return upstream_error(exc)


@api_view(["POST"])
def plan_view(request):
    """
    POST {
        origin: {lat, lon}, destination: {lat, lon},
        departure_iso: "2026-10-21T08:00:00Z",
        load_lb: 35000, sample_miles: 25        # 10 | 25 | 50
    }
    -> {routes: [...], recommended: "route-1"}
    Each route: id, distance_mi, duration_min, geometry, checkpoints
    [{lat, lon, mile, eta_iso, wind_mph, precip_in, snow_in, risk,
      risk_label, color}], summary {miles_by_risk, avg_risk}, avg_risk.
    """
    try:
        origin = request.data["origin"]
        destination = request.data["destination"]
        departure = datetime.fromisoformat(request.data["departure_iso"])
        if departure.tzinfo is None:
            departure = departure.replace(tzinfo=timezone.utc)
        load_lb = float(request.data.get("load_lb", 0))
        sample_miles = float(request.data.get("sample_miles", 25))
    except (KeyError, ValueError, TypeError) as exc:
        return Response({"error": f"invalid payload: {exc}"}, status=400)

    if sample_miles not in ALLOWED_SAMPLE_MILES:
        return Response(
            {"error": "sample_miles must be one of 10, 25, 50"}, status=400
        )
    if load_lb < 0:
        return Response({"error": "load_lb must be >= 0"}, status=400)

    try:
        routes = fetch_routes(origin, destination)
        # Enrich routes concurrently: each route's weather batch is an
        # independent upstream call, so running them in parallel cuts the
        # response time roughly to the slowest single route.
        with ThreadPoolExecutor(max_workers=len(routes) or 1) as pool:
            enriched = list(
                pool.map(
                    lambda r: _enrich_route(r, departure, sample_miles, load_lb),
                    routes,
                )
            )
    except (requests.RequestException, RuntimeError) as exc:
        return upstream_error(exc)

    return Response({"routes": enriched, "recommended": recommend_route(enriched)})


@api_view(["POST"])
def weather_view(request):
    """
    Re-score weather + risk for existing checkpoints at new ETAs.
    Powers the 0–48h forecast slider without re-routing.
    POST {checkpoints: [{lat, lon, mile, eta_iso}], load_lb}
      -> {checkpoints: [...scored...], summary: {miles_by_risk, avg_risk}}
    """
    try:
        load_lb = float(request.data.get("load_lb", 0))
        raw = request.data["checkpoints"]
        cps = [
            {
                "lat": float(c["lat"]),
                "lon": float(c["lon"]),
                "mile": float(c.get("mile", 0)),
                "eta": datetime.fromisoformat(c["eta_iso"]),
            }
            for c in raw
        ]
    except (KeyError, ValueError, TypeError) as exc:
        return Response({"error": f"invalid payload: {exc}"}, status=400)

    try:
        weather = fetch_weather_batch(cps)
    except requests.RequestException as exc:
        return upstream_error(exc)

    out = []
    for cp, w in zip(cps, weather):
        scored = score_checkpoint(dict(cp), w, load_lb)
        scored["eta_iso"] = scored.pop("eta").isoformat()
        out.append(scored)

    return Response({"checkpoints": out, "summary": summarize(out)})
