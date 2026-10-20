"""
End-to-end smoke test: boots the Django dev server, exercises all three
endpoints with a real Dallas -> Chicago plan, and shuts the server down.

Run:  python tests_smoke.py     (from backend/, venv active)
Requires internet (OSRM / Open-Meteo / Nominatim).
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta

import requests

BASE = "http://127.0.0.1:8000"
FAILURES = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILURES.append(name)


def wait_for_server(proc, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        try:
            requests.post(f"{BASE}/api/geocode", json={}, timeout=2)
            return
        except requests.RequestException:
            time.sleep(0.5)
    raise RuntimeError("server did not come up in time")


def main():
    proc = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", "127.0.0.1:8000", "--noreload"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_server(proc)

        # --- geocode
        r = requests.post(f"{BASE}/api/geocode", json={"q": "Dallas, TX"}, timeout=30)
        check("geocode 200", r.status_code == 200, f"(got {r.status_code})")
        results = r.json()["results"]
        check("geocode finds Dallas", len(results) > 0 and "Dallas" in results[0]["label"])

        # --- plan: Dallas -> Chicago, 25-mile sampling, 35,000 lb
        payload = {
            "origin": {"lat": 32.7767, "lon": -96.7970},
            "destination": {"lat": 41.8781, "lon": -87.6298},
            "departure_iso": "2026-10-21T08:00:00Z",
            "load_lb": 35000,
            "sample_miles": 25,
        }
        r = requests.post(f"{BASE}/api/plan", json=payload, timeout=120)
        check("plan 200", r.status_code == 200, f"(got {r.status_code})")
        plan = r.json()
        routes = plan["routes"]
        check("plan returns 3 routes", len(routes) == 3, f"(got {len(routes)})")
        check("recommended is one of the routes",
              plan["recommended"] in {rt["id"] for rt in routes})
        for rt in routes:
            cps = rt["checkpoints"]
            miles = [c["mile"] for c in cps]
            spacing_ok = all(
                abs((miles[i + 1] - miles[i]) - 25) < 0.2
                for i in range(len(miles) - 2)
            )
            total_by_risk = sum(rt["summary"]["miles_by_risk"].values())
            check(f"{rt['id']}: {rt['distance_mi']} mi, {len(cps)} checkpoints, "
                  f"25-mi spacing, risk-miles sum ~= distance",
                  spacing_ok and abs(total_by_risk - miles[-1]) < 0.5,
                  f"(risk sum {total_by_risk:.1f} vs last mile {miles[-1]})")
            check(f"{rt['id']}: every checkpoint scored",
                  all(0 <= c["risk"] <= 4 and c["risk_label"] for c in cps))

        # --- weather: re-score route-1 at +24h
        cps = routes[0]["checkpoints"][:4]
        shifted = [
            {
                "lat": c["lat"],
                "lon": c["lon"],
                "mile": c["mile"],
                "eta_iso": (datetime.fromisoformat(c["eta_iso"]) + timedelta(hours=24)).isoformat(),
            }
            for c in cps
        ]
        r = requests.post(f"{BASE}/api/weather",
                          json={"checkpoints": shifted, "load_lb": 35000}, timeout=60)
        check("weather 200", r.status_code == 200, f"(got {r.status_code})")
        w = r.json()
        check("weather returns scored checkpoints + summary",
              len(w["checkpoints"]) == 4 and "miles_by_risk" in w["summary"])

        # --- validation: sample_miles must be 10/25/50
        bad = dict(payload, sample_miles=7)
        r = requests.post(f"{BASE}/api/plan", json=bad, timeout=30)
        check("plan rejects sample_miles=7 with 400", r.status_code == 400,
              f"(got {r.status_code})")
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
