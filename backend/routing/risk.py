"""
Weather risk engine for the Spotter AI assessment.

Risk levels (ascending severity):
    0 = Low, 1 = Moderate, 2 = High, 3 = Severe, 4 = No Travel

The wind rules with the load-weight thresholds come straight from the
assessment brief. Rain/snow bands are the assessor-provided tables
(Low..Severe..No Travel) implemented per their cutoffs.
"""

LOW, MODERATE, HIGH, SEVERE, NO_TRAVEL = 0, 1, 2, 3, 4

RISK_LABELS = {
    LOW: "Low",
    MODERATE: "Moderate",
    HIGH: "High",
    SEVERE: "Severe",
    NO_TRAVEL: "No Travel",
}

# Map colors for the frontend (green -> amber -> orange -> red -> dark red)
RISK_COLORS = {
    LOW: "#22c55e",
    MODERATE: "#eab308",
    HIGH: "#f97316",
    SEVERE: "#ef4444",
    NO_TRAVEL: "#7f1d1d",
}


def wind_risk(wind_mph: float, load_lb: float) -> int:
    """Wind risk, with the assessment's load-weight rules."""
    if wind_mph >= 55:
        return NO_TRAVEL  # any load
    if wind_mph >= 45:
        return NO_TRAVEL if load_lb > 30_000 else SEVERE
    if wind_mph >= 35:
        return SEVERE if load_lb > 40_000 else HIGH
    if wind_mph >= 20:
        return MODERATE
    return LOW


def rain_risk(precip_in_per_hr: float) -> int:
    """Rain risk from hourly precipitation (inches)."""
    if precip_in_per_hr > 0.50:
        return SEVERE
    if precip_in_per_hr > 0.25:
        return HIGH
    if precip_in_per_hr > 0.05:
        return MODERATE
    return LOW


def snow_risk(snowfall_in_per_hr: float) -> int:
    """Snow risk from hourly snowfall (inches)."""
    if snowfall_in_per_hr > 1.0:
        return SEVERE
    if snowfall_in_per_hr > 0.5:
        return HIGH
    if snowfall_in_per_hr > 0.1:
        return MODERATE
    return LOW


def checkpoint_risk(wind_mph: float, precip_in: float, snow_in: float, load_lb: float) -> int:
    """A checkpoint's overall risk is the worst of wind / rain / snow."""
    return max(
        wind_risk(wind_mph, load_lb),
        rain_risk(precip_in),
        snow_risk(snow_in),
    )


def recommend_route(routes: list[dict]) -> str | None:
    """
    Pick the best route per the assessment rules, in priority order:
      1. fewest Severe/No-Travel miles
      2. fewest High miles
      3. lowest average risk
      4. shortest travel time
    Each route dict must have keys: id, summary {miles_by_risk}, avg_risk,
    duration_sec. Returns the winning route id.
    """
    def sort_key(r):
        summary = r["summary"]["miles_by_risk"]
        bad_miles = summary.get(str(SEVERE), 0) + summary.get(str(NO_TRAVEL), 0)
        high_miles = summary.get(str(HIGH), 0)
        return (bad_miles, high_miles, r["avg_risk"], r["duration_sec"])

    ranked = sorted(routes, key=sort_key)
    return ranked[0]["id"] if ranked else None


def summarize(checkpoints: list[dict]) -> dict:
    """
    Miles per risk band + distance-weighted average risk across a route.

    Each checkpoint "owns" the road from its own mile marker up to the
    next checkpoint's; the destination owns none. Spans come from the
    checkpoints' `mile` values, so a trailing partial segment (e.g. the
    last 12 miles of a 50-mile sampling interval) is counted exactly
    instead of being rounded up to a full interval.
    Checkpoints must carry `mile` and `risk` keys.
    """
    miles_by_risk = {str(level): 0.0 for level in range(5)}
    weighted = 0.0
    total = 0.0
    for i, cp in enumerate(checkpoints):
        span = 0.0
        if i + 1 < len(checkpoints):
            span = max(checkpoints[i + 1]["mile"] - cp["mile"], 0.0)
        level = cp["risk"]
        miles_by_risk[str(level)] = round(miles_by_risk[str(level)] + span, 2)
        weighted += level * span
        total += span
    if total == 0:
        # Degenerate route (single point): fall back to a plain mean.
        avg = (
            sum(cp["risk"] for cp in checkpoints) / len(checkpoints)
            if checkpoints
            else 0.0
        )
        return {"miles_by_risk": miles_by_risk, "avg_risk": round(avg, 2)}
    return {
        "miles_by_risk": miles_by_risk,
        "avg_risk": round(weighted / total, 2),
    }
