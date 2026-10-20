# RouteGuard — Weather-Aware Truck Routing

A full-stack web app that helps a truck dispatcher/driver pick the **safest**
of three route options — not just the fastest — by sampling the weather
forecast at the truck's **estimated arrival time** at regular checkpoints
along each route and scoring it with wind/rain/snow risk tables plus
load-weight safety rules.

Built for the Spotter AI Full-Stack Developer take-home assessment
(React + Django). All third-party services are **free and require no API
keys**: OSRM (routing), Open-Meteo (weather), Nominatim/OpenStreetMap
(geocoding + map tiles).

![stack](https://img.shields.io/badge/frontend-React%20%2B%20Leaflet-blue)
![stack](https://img.shields.io/badge/backend-Django%20%2B%20DRF-green)

---

## Features

- **3 route options** for any origin/destination, drawn on an interactive
  map with distance, drive time, and a per-route risk breakdown.
- **Weather sampled every 10 / 25 / 50 miles** (user-selectable) along each
  route — at the truck's **ETA at that checkpoint**, not at departure time.
- **Risk engine** implementing the assessment's tables, including the
  load-weight wind rules:
  - wind **≥ 55 mph → No Travel** (any load)
  - wind **45–54 mph + load > 30,000 lb → No Travel**
  - wind **35–44 mph + load > 40,000 lb → Severe**
  - rain and snow band tables; a checkpoint's risk is the worst of the three.
- **Recommendation** picks the best route in the assessment's priority
  order: fewest Severe/No-Travel miles → fewest High miles → lowest
  average risk → shortest travel time.
- **Weather heatmap** along the trip corridor (toggleable), risk-colored
  checkpoint markers with detail popups, and a full checkpoint table
  (mile, ETA, wind, rain, snow, risk badge).
- **0–48h forecast slider** — “what if I leave later?” Re-scores the
  selected route at departure + N hours (no re-routing) and updates its
  summary card.
- Clean loading/error states and a responsive layout (panel stacks above
  the map on mobile).

## Quick start (local)

Prereqs: Python 3.12+, Node 18+.

```bash
# 1) Backend  →  http://localhost:8000
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python manage.py runserver

# 2) Frontend →  http://localhost:5173   (new terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173, type an origin (e.g. *Dallas, TX*) and a
destination (e.g. *Chicago, IL*), pick them from the suggestions, set the
departure time, load weight and sampling interval, and hit
**Find safest route**. In dev, Vite proxies `/api` to the backend, so no
configuration is needed.

## Tests

```bash
cd backend && source venv/bin/activate

# Risk-engine + sampling + recommendation rule checks (36 assertions,
# no network needed) — verifies the brief's exact wind/load thresholds,
# boundary values, checkpoint spacing, ETA math and tie-breaking order.
python tests_check.py

# End-to-end smoke test: boots the server and exercises all three API
# endpoints with a real Dallas → Chicago plan (needs internet).
python tests_smoke.py
```

## How it works

### Architecture

```
frontend/  React + Vite + react-leaflet
           └─ POST /api/* ──► backend/  Django + DRF (stateless, no DB)
                                  ├─ Nominatim  — place search
                                  ├─ OSRM       — 3 route alternatives
                                  └─ Open-Meteo — hourly forecast per point
```

The backend is a pure computation service — no database, no auth.
Endpoints:

| Endpoint | Input | Output |
|---|---|---|
| `POST /api/geocode` | `{q}` | `[{label, lat, lon}]` |
| `POST /api/plan` | `{origin, destination, departure_iso, load_lb, sample_miles}` | routes (geometry, checkpoints, summary) + `recommended` id |
| `POST /api/weather` | `{checkpoints: [{lat, lon, mile, eta_iso}], load_lb}` | re-scored checkpoints + summary (powers the forecast slider) |

### ETA-based weather sampling

For each route, checkpoints are placed every *N* miles along the OSRM
polyline by walking the geometry and interpolating positions
(`routing/services.py::sample_checkpoints`). OSRM returns one total
duration per route, so driving time is distributed proportionally to
distance: `ETA(checkpoint) = departure + (mile / route_miles) × duration`.
Each checkpoint's weather is then the Open-Meteo hourly forecast **nearest
to that ETA** — a storm the truck reaches at hour 9 is scored from the
hour-9 forecast, not from departure-time weather. All checkpoints of a
route are fetched in a single batched Open-Meteo call.

Risk accounting is exact: each checkpoint “owns” the road up to the next
checkpoint, so miles-per-risk-band always sum to the route length,
including the final partial segment.

### Getting 3 routes out of OSRM

OSRM's public server returns at most 2 alternatives (some corridors only
1). When fewer than 3 come back, `fetch_routes` tops up with **via-point
routes**: it routes through a point offset perpendicular from an existing
corridor, which makes OSRM compute a genuinely different third option on
real roads. Candidates are rejected if they overlap an existing corridor
(>80% of sampled points within 3 miles with a near-identical length) or
are absurd detours (>35% longer than the shortest option).

### Risk tables (`routing/risk.py`)

Levels: Low < Moderate < High < Severe < No Travel. Checkpoint risk =
max(wind, rain, snow).

- **Wind** (mph, with load rules): ≥55 → No Travel · 45–54 → No Travel if
  load > 30,000 lb else Severe · 35–44 → Severe if load > 40,000 lb else
  High · 20–34 → Moderate · <20 → Low.
- **Rain** (in/hr): >0.50 → Severe · >0.25 → High · >0.05 → Moderate.
- **Snow** (in/hr): >1.0 → Severe · >0.5 → High · >0.1 → Moderate.

## Deploy (free tier, ~10 minutes)

A Render **blueprint** is included (`render.yaml`) that creates both
services:

1. Push this repo to GitHub, then on [render.com](https://render.com):
   **New → Blueprint**, select the repo, **Apply**.
2. The API deploys first. Copy its URL (e.g.
   `https://routeguard-api.onrender.com`).
3. In the **routeguard-web** service → Environment, set
   `VITE_API_BASE=https://routeguard-api.onrender.com/api` and redeploy.

Environment variables:

| Service | Variable | Value |
|---|---|---|
| backend | `DJANGO_DEBUG` | `0` in production |
| backend | `DJANGO_SECRET_KEY` | any long random string (blueprint generates one) |
| backend | `ALLOWED_HOSTS` | `*` (or your API hostname) |
| backend | `OSRM_BASE_URL` | optional; defaults to the public OSRM server |
| frontend (build-time) | `VITE_API_BASE` | `https://<backend-host>/api` — locally unset, the Vite dev proxy is used |

**Alternatives.** Backend on Fly.io: `cd backend && fly launch --no-deploy`,
set the secrets above, `fly deploy` (start command
`gunicorn config.wsgi:application`). Frontend on Netlify: `netlify.toml`
at the repo root is preconfigured (base `frontend`, publish `dist`) — just
set `VITE_API_BASE` in the Netlify UI. On Vercel: import the repo, set the
root directory to `frontend`, add `VITE_API_BASE`. Note Render's free web
services sleep after inactivity, so the first request may take ~30 s.

## Loom walkthrough outline (5–10 min)

1. **The problem** (0:00–0:45) — fastest ≠ safest for a loaded truck;
   weather risk depends on *when* the truck reaches each point.
2. **Live demo** (0:45–3:00) — Dallas → Chicago, 35,000 lb, 25-mile
   sampling: the 3 routes, recommended badge, checkpoint markers and
   popups, risk bar on each card.
3. **Change an input** (3:00–4:00) — raise the load past 30k/45k
   thresholds and show checkpoints flipping to Severe/No Travel per the
   wind rules; switch sampling to 10 miles.
4. **Forecast slider + heatmap** (4:00–5:00) — drag +24h and show risk
   re-scoring at the shifted ETAs; toggle the corridor heatmap.
5. **Under the hood** (5:00–7:30) — architecture diagram in words:
   Django/DRF stateless API; OSRM (+ via-point top-up for the 3rd route);
   batched Open-Meteo calls at ETA hours; the risk engine and the
   recommendation priority order; `tests_check.py` proving the rules.
6. **With more time** (7:30–8:30) — response caching, per-segment speeds
   from OSRM legs instead of proportional ETAs, weather alerts/push,
   self-hosted OSRM for reliability.

## Limitations & future work

- ETAs assume a constant average speed per route (OSRM gives one total
  duration); per-leg durations would sharpen checkpoint ETAs.
- Weather is the nearest forecast *hour* to each ETA; Open-Meteo's free
  forecast horizon is 16 days, which bounds how far ahead trips can be
  planned.
- Public OSRM/Open-Meteo are rate-limited community services; production
  would self-host OSRM and cache forecast responses per corridor/hour.

## Project layout

```
backend/
  config/        Django settings & root URLs
  routing/
    risk.py      Risk tables, load rules, recommendation engine
    services.py  Nominatim / OSRM / Open-Meteo clients, checkpoint sampler
    views.py     /api/geocode, /api/plan, /api/weather
  tests_check.py Rule verification (36 assertions)
  tests_smoke.py End-to-end endpoint smoke test
frontend/
  src/App.jsx    Map, route cards, slider, checkpoint table, legend
  src/api.js     API client (VITE_API_BASE)
render.yaml      Render blueprint (backend + frontend)
netlify.toml     Netlify config (frontend alternative)
```
