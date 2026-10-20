"""
Verification checks for the Spotter assessment's exact rules.

Run:  python tests_check.py     (from backend/, venv active)

Covers:
  * Wind/load rules from the brief:
      >=55 mph              -> No Travel, any load
      45-54 mph + >30,000lb -> No Travel
      35-44 mph + >40,000lb -> Severe
  * Checkpoint sampling every N miles with ETA-at-checkpoint math
  * Recommendation ordering: fewest Severe/No-Travel miles ->
    fewest High miles -> lowest avg risk -> shortest time
  * summarize() miles-by-risk accounting from checkpoint mile spans
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from routing.risk import (  # noqa: E402
    HIGH,
    LOW,
    MODERATE,
    NO_TRAVEL,
    SEVERE,
    checkpoint_risk,
    recommend_route,
    summarize,
    wind_risk,
)
from routing.services import sample_checkpoints  # noqa: E402

FAILURES = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {actual}, want {expected}")
    if not ok:
        FAILURES.append(name)


# ---------------------------------------------------------------- wind rules
# >=55 mph -> No Travel for ANY load
check("wind 55, load 0", wind_risk(55, 0), NO_TRAVEL)
check("wind 80, load 80000", wind_risk(80, 80_000), NO_TRAVEL)
check("wind 120, load 10000", wind_risk(120, 10_000), NO_TRAVEL)

# 45-54 mph + load > 30,000 lb -> No Travel; at exactly 30,000 -> not No Travel
check("wind 54.9, load 30001", wind_risk(54.9, 30_001), NO_TRAVEL)
check("wind 45, load 30001", wind_risk(45, 30_001), NO_TRAVEL)
check("wind 45, load 30000 (boundary)", wind_risk(45, 30_000), SEVERE)
check("wind 50, load 20000", wind_risk(50, 20_000), SEVERE)

# 35-44 mph + load > 40,000 lb -> Severe; at exactly 40,000 -> High
check("wind 44.9, load 40001", wind_risk(44.9, 40_001), SEVERE)
check("wind 35, load 40001", wind_risk(35, 40_001), SEVERE)
check("wind 35, load 40000 (boundary)", wind_risk(35, 40_000), HIGH)
check("wind 40, load 10000", wind_risk(40, 10_000), HIGH)

# Lower bands
check("wind 34.9", wind_risk(34.9, 0), MODERATE)
check("wind 20", wind_risk(20, 0), MODERATE)
check("wind 19.9", wind_risk(19.9, 0), LOW)
check("wind 0", wind_risk(0, 0), LOW)

# checkpoint risk = worst of wind/rain/snow
check("rain dominates", checkpoint_risk(5, 0.30, 0.0, 0), HIGH)
check("snow dominates", checkpoint_risk(5, 0.0, 0.6, 0), HIGH)
check("wind load rule dominates", checkpoint_risk(50, 0.0, 0.0, 35_000), NO_TRAVEL)
check("all calm", checkpoint_risk(5, 0.01, 0.0, 0), LOW)

# ------------------------------------------------------- checkpoint sampling
# Straight east-west line ~ Dallas-ish latitude; total length computed by the
# sampler itself. 0.5 deg of longitude at lat 39 ~= 26.8 miles.
geometry = [[39.0, -98.0 + 0.05 * i] for i in range(21)]  # ~53.7 mi total
departure = datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc)
cps = sample_checkpoints(geometry, duration_sec=3600, departure=departure,
                         every_miles=25)
miles = [c["mile"] for c in cps]
check("sampling: first checkpoint at mile 0", miles[0], 0.0)
check("sampling: interior checkpoints at 25, 50",
      [m for m in miles if m in (25.0, 50.0)], [25.0, 50.0])
check("sampling: last checkpoint is destination",
      (cps[-1]["lat"], cps[-1]["lon"]), (39.0, -97.0))
check("sampling: ETAs strictly increasing",
      all(cps[i]["eta"] < cps[i + 1]["eta"] for i in range(len(cps) - 1)), True)
check("sampling: final ETA = departure + duration",
      cps[-1]["eta"], departure + timedelta(seconds=3600))
# ETA at the 25-mile checkpoint should be ~25/total of the hour.
total = miles[-1]
eta25 = next(c["eta"] for c in cps if c["mile"] == 25.0)
expected25 = departure + timedelta(seconds=3600 * 25 / total)
check("sampling: ETA proportional to distance",
      abs((eta25 - expected25).total_seconds()) < 60, True)

cps10 = sample_checkpoints(geometry, 3600, departure, every_miles=10)
spacings = [round(cps10[i + 1]["mile"] - cps10[i]["mile"], 1)
            for i in range(len(cps10) - 1)]
check("sampling: 10-mi spacing (except final partial)",
      all(s == 10.0 for s in spacings[:-1]), True)

# ------------------------------------------------------------- summarize()
fake = [
    {"mile": 0.0, "risk": LOW},
    {"mile": 25.0, "risk": SEVERE},
    {"mile": 50.0, "risk": HIGH},
    {"mile": 62.0, "risk": NO_TRAVEL},  # destination: owns no forward miles
]
s = summarize(fake)
check("summarize: severe miles", s["miles_by_risk"][str(SEVERE)], 25.0)
check("summarize: high miles", s["miles_by_risk"][str(HIGH)], 12.0)
check("summarize: low miles", s["miles_by_risk"][str(LOW)], 25.0)
check("summarize: total = route length",
      sum(s["miles_by_risk"].values()), 62.0)
check("summarize: avg risk weighted",
      s["avg_risk"], round((0 * 25 + 3 * 25 + 2 * 12) / 62, 2))

# ------------------------------------------------------- recommendation
def route(rid, bad, high, avg, dur):
    summary = {"miles_by_risk": {str(SEVERE): bad, str(HIGH): high},
               "avg_risk": avg}
    return {"id": rid, "summary": summary, "avg_risk": avg,
            "duration_sec": dur}

# Rule 1: fewest Severe/No-Travel miles beats everything else
check("recommend: fewest bad miles wins",
      recommend_route([route("a", 10, 0, 0.5, 100), route("b", 5, 99, 3.0, 999)]),
      "b")
# Rule 2: tie on bad miles -> fewest High miles
check("recommend: then fewest high miles",
      recommend_route([route("a", 5, 30, 0.5, 100), route("b", 5, 10, 3.0, 999)]),
      "b")
# Rule 3: tie on both -> lowest average risk
check("recommend: then lowest avg risk",
      recommend_route([route("a", 5, 10, 1.5, 100), route("b", 5, 10, 1.2, 999)]),
      "b")
# Rule 4: tie on all -> shortest travel time
check("recommend: then shortest time",
      recommend_route([route("a", 5, 10, 1.2, 500), route("b", 5, 10, 1.2, 300)]),
      "b")
# No-Travel miles count the same as Severe in rule 1
nt_route = {"id": "nt",
            "summary": {"miles_by_risk": {str(NO_TRAVEL): 5, str(HIGH): 0},
                        "avg_risk": 4.0},
            "avg_risk": 4.0, "duration_sec": 100}
check("recommend: No-Travel miles count as bad miles",
      recommend_route([route("a", 6, 0, 0.1, 50), nt_route]), "nt")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("ALL CHECKS PASSED")
