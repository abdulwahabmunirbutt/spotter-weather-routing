import { useEffect, useMemo, useRef, useState } from "react";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  Popup,
  TileLayer,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import "leaflet.heat";
import { geocode, planRoute, rescoreWeather } from "./api";

const PLAN_STEPS = [
  "Finding routes…",
  "Sampling weather along the corridor…",
  "Scoring risk for each route…",
  "Picking the safest route…",
];

const RISK_LEVELS = [
  { label: "Low", color: "#22c55e" },
  { label: "Moderate", color: "#eab308" },
  { label: "High", color: "#f97316" },
  { label: "Severe", color: "#ef4444" },
  { label: "No Travel", color: "#7f1d1d" },
];

/** Fits the map to all route geometries when they change. */
function FitBounds({ routes }) {
  const map = useMap();
  useEffect(() => {
    if (!routes.length) return;
    const bounds = L.latLngBounds(
      routes.flatMap((r) => r.geometry.map(([lat, lon]) => [lat, lon]))
    );
    map.fitBounds(bounds, { padding: [40, 40] });
  }, [routes, map]);
  return null;
}

/** Weather heatmap layer along the trip corridor (L.heatLayer via ref). */
function Heatmap({ points }) {
  const map = useMap();
  const layerRef = useRef(null);
  useEffect(() => {
    if (layerRef.current) {
      map.removeLayer(layerRef.current);
      layerRef.current = null;
    }
    if (points.length) {
      layerRef.current = L.heatLayer(points, {
        radius: 28,
        blur: 22,
        maxZoom: 10,
        gradient: {
          0.2: "#22c55e",
          0.45: "#eab308",
          0.65: "#f97316",
          0.85: "#ef4444",
          1: "#7f1d1d",
        },
      }).addTo(map);
    }
    return () => {
      if (layerRef.current) map.removeLayer(layerRef.current);
    };
  }, [points, map]);
  return null;
}

function fmtEta(iso) {
  const d = new Date(iso);
  return d.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtDuration(min) {
  return `${Math.floor(min / 60)}h ${String(min % 60).padStart(2, "0")}m`;
}

export default function App() {
  const [originText, setOriginText] = useState("");
  const [destText, setDestText] = useState("");
  const [originOpts, setOriginOpts] = useState([]);
  const [destOpts, setDestOpts] = useState([]);
  const [origin, setOrigin] = useState(null);
  const [destination, setDestination] = useState(null);
  const [departure, setDeparture] = useState("");
  const [loadLb, setLoadLb] = useState(35000);
  const [sampleMiles, setSampleMiles] = useState(25);
  const [forecastHrs, setForecastHrs] = useState(0);
  const [showHeat, setShowHeat] = useState(true);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingKind, setLoadingKind] = useState("plan");
  const [stepIdx, setStepIdx] = useState(0);

  // Cycle the progress narration while a plan is being computed.
  useEffect(() => {
    if (!loading || loadingKind !== "plan") return;
    setStepIdx(0);
    const t = setInterval(
      () => setStepIdx((i) => Math.min(i + 1, PLAN_STEPS.length - 1)),
      1600
    );
    return () => clearInterval(t);
  }, [loading, loadingKind]);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);

  // Default departure = tomorrow 08:00 local.
  useEffect(() => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(8, 0, 0, 0);
    const pad = (n) => String(n).padStart(2, "0");
    setDeparture(
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
        d.getHours()
      )}:${pad(d.getMinutes())}`
    );
  }, []);

  const debounceRef = useRef(null);
  const searchPlaces = (q, setter) => {
    clearTimeout(debounceRef.current);
    if (q.trim().length < 3) return setter([]);
    debounceRef.current = setTimeout(async () => {
      try {
        const { results } = await geocode(q);
        setter(results.slice(0, 5));
      } catch {
        setter([]);
      }
    }, 400);
  };

  const handlePlan = async () => {
    if (!origin || !destination || !departure) {
      setError("Pick an origin, a destination and a departure time.");
      return;
    }
    setLoading(true);
    setLoadingKind("plan");
    setError("");
    setResult(null);
    try {
      const data = await planRoute({
        origin,
        destination,
        departure_iso: new Date(departure).toISOString(),
        load_lb: Number(loadLb),
        sample_miles: Number(sampleMiles),
      });
      setResult(data);
      setSelected(data.recommended);
      setForecastHrs(0);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  // Forecast slider: re-score the selected route's checkpoints as if the
  // whole trip started N hours later (departure + N), without re-routing.
  // The backend returns a fresh route summary, so the cards stay truthful.
  const sliderTimer = useRef(null);
  const handleSlider = (hrs) => {
    setForecastHrs(hrs);
    if (!result || !selected) return;
    clearTimeout(sliderTimer.current);
    sliderTimer.current = setTimeout(async () => {
      if (hrs === 0) return handlePlan(); // back to the original plan
      const route = resultRef.current?.routes.find((r) => r.id === selected);
      if (!route) return;
      setLoading(true);
      setLoadingKind("weather");
      setError("");
      try {
        const t0 = new Date(route.checkpoints[0].eta_iso).getTime();
        const cps = route.checkpoints.map((c) => ({
          lat: c.lat,
          lon: c.lon,
          mile: c.mile,
          eta_iso: new Date(
            t0 + hrs * 3600_000 + (new Date(c.eta_iso) - t0)
          ).toISOString(),
        }));
        const data = await rescoreWeather({
          checkpoints: cps,
          load_lb: Number(loadLb),
        });
        setResult((prev) => ({
          ...prev,
          routes: prev.routes.map((r) =>
            r.id === selected
              ? {
                  ...r,
                  checkpoints: data.checkpoints.map((c, i) => ({
                    ...r.checkpoints[i],
                    ...c,
                  })),
                  summary: data.summary,
                  avg_risk: data.summary.avg_risk,
                }
              : r
          ),
        }));
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    }, 300);
  };

  // Keep a ref of the latest result for the debounced slider handler.
  const resultRef = useRef(null);
  resultRef.current = result;

  const shownRoute = useMemo(
    () => result?.routes.find((r) => r.id === selected) || result?.routes[0],
    [result, selected]
  );

  const heatPoints = useMemo(
    () =>
      showHeat && shownRoute
        ? shownRoute.checkpoints.map((c) => [c.lat, c.lon, (c.risk + 1) / 5])
        : [],
    [shownRoute, showHeat]
  );

  const placeField = (label, text, setText, opts, setOpts, setPlace, placeholder) => (
    <label>
      {label}
      <input
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setPlace(null);
          searchPlaces(e.target.value, setOpts);
        }}
        placeholder={placeholder}
      />
      {opts.length > 0 && (
        <ul className="suggest">
          {opts.map((o, i) => (
            <li
              key={i}
              onClick={() => {
                setPlace(o);
                setText(o.label.split(",").slice(0, 2).join(","));
                setOpts([]);
              }}
            >
              {o.label}
            </li>
          ))}
        </ul>
      )}
    </label>
  );

  return (
    <div className="app">
      <aside className="panel">
        <h1>🚚 RouteGuard</h1>
        <p className="sub">
          Weather-aware truck routing — pick the safest route, not just the
          fastest.
        </p>

        {placeField(
          "Origin", originText, setOriginText, originOpts, setOriginOpts,
          setOrigin, "e.g. Dallas, TX"
        )}
        {placeField(
          "Destination", destText, setDestText, destOpts, setDestOpts,
          setDestination, "e.g. Chicago, IL"
        )}

        <div className="row">
          <label>
            Departure
            <input
              type="datetime-local"
              value={departure}
              onChange={(e) => setDeparture(e.target.value)}
            />
          </label>
          <label>
            Load (lb)
            <input
              type="number"
              value={loadLb}
              min={0}
              step={1000}
              onChange={(e) => setLoadLb(e.target.value)}
            />
          </label>
        </div>

        <label>
          Weather check every
          <select
            value={sampleMiles}
            onChange={(e) => setSampleMiles(e.target.value)}
          >
            <option value={10}>10 miles</option>
            <option value={25}>25 miles</option>
            <option value={50}>50 miles</option>
          </select>
        </label>

        <button onClick={handlePlan} disabled={loading}>
          {loading && loadingKind === "plan" ? (
            <>
              <span className="btnspin" /> Planning…
            </>
          ) : (
            "Find safest route"
          )}
        </button>
        {error && <p className="error">⚠️ {error}</p>}

        {result && (
          <>
            <h2>Route options</h2>
            <div className="cards">
              {result.routes.map((r) => (
                <div
                  key={r.id}
                  className={`card ${r.id === selected ? "selected" : ""} ${
                    r.id === result.recommended ? "best" : ""
                  }`}
                  onClick={() => setSelected(r.id)}
                >
                  <div className="card-head">
                    <strong>
                      {r.id === result.recommended ? "⭐ " : ""}Route{" "}
                      {r.id.split("-")[1]}
                      {r.id === result.recommended && (
                        <span className="rec">Recommended</span>
                      )}
                    </strong>
                    <span>
                      {r.distance_mi} mi · {fmtDuration(r.duration_min)}
                    </span>
                  </div>
                  <div className="riskbar">
                    {RISK_LEVELS.map((lvl, level) =>
                      r.summary.miles_by_risk[String(level)] > 0 ? (
                        <span
                          key={level}
                          title={`${lvl.label}: ${r.summary.miles_by_risk[String(level)]} mi`}
                          style={{
                            flex: r.summary.miles_by_risk[String(level)],
                            background: lvl.color,
                          }}
                        />
                      ) : null
                    )}
                  </div>
                  <div className="card-foot">
                    avg risk {r.avg_risk.toFixed(2)} · worst:{" "}
                    {worstLabel(r.summary.miles_by_risk)}
                  </div>
                </div>
              ))}
            </div>

            <h2>
              Forecast slider <span className="pill">depart +{forecastHrs}h</span>
            </h2>
            <input
              type="range"
              min={0}
              max={48}
              step={1}
              value={forecastHrs}
              onChange={(e) => handleSlider(Number(e.target.value))}
              className="slider"
            />
            <p className="hint">
              “What if I leave later?” — re-scores the selected route’s
              checkpoints (and its card above) at departure + N hours.
            </p>

            {shownRoute && (
              <>
                <h2>Checkpoints — Route {shownRoute.id.split("-")[1]}</h2>
                <div className="tablewrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Mile</th>
                        <th>ETA</th>
                        <th>Wind</th>
                        <th>Rain</th>
                        <th>Snow</th>
                        <th>Risk</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shownRoute.checkpoints.map((c, i) => (
                        <tr key={i}>
                          <td>{c.mile}</td>
                          <td>{fmtEta(c.eta_iso)}</td>
                          <td>{c.wind_mph.toFixed(0)} mph</td>
                          <td>{c.precip_in.toFixed(2)}″</td>
                          <td>{c.snow_in.toFixed(2)}″</td>
                          <td>
                            <span
                              className="badge"
                              style={{ background: c.color }}
                            >
                              {c.risk_label}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </aside>

      <main className="mapwrap">
        <MapContainer center={[39.8, -98.6]} zoom={4} className="map">
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution="© OpenStreetMap contributors"
          />
          {result && <FitBounds routes={result.routes} />}
          {result &&
            result.routes.map((r) => (
              <Polyline
                key={r.id}
                positions={r.geometry}
                color={r.id === selected ? "#2563eb" : "#9ca3af"}
                weight={r.id === selected ? 6 : 3}
                opacity={r.id === selected ? 1 : 0.55}
              />
            ))}
          {shownRoute &&
            shownRoute.checkpoints.map((c, i) => (
              <CircleMarker
                key={`${shownRoute.id}-${i}`}
                center={[c.lat, c.lon]}
                radius={7}
                pathOptions={{
                  color: "#fff",
                  weight: 2,
                  fillColor: c.color,
                  fillOpacity: 0.95,
                }}
              >
                <Popup>
                  <strong>Mile {c.mile}</strong> — {c.risk_label}
                  <br />
                  ETA {fmtEta(c.eta_iso)}
                  <br />
                  Wind {c.wind_mph.toFixed(0)} mph · Rain{" "}
                  {c.precip_in.toFixed(2)}″ · Snow {c.snow_in.toFixed(2)}″
                </Popup>
              </CircleMarker>
            ))}
          <Heatmap points={heatPoints} />
        </MapContainer>

        <div className="legend">
          <div className="legend-title">Weather risk</div>
          {RISK_LEVELS.map((l) => (
            <div key={l.label} className="legend-row">
              <span className="dot" style={{ background: l.color }} />
              {l.label}
            </div>
          ))}
          <label className="heat-toggle">
            <input
              type="checkbox"
              checked={showHeat}
              onChange={(e) => setShowHeat(e.target.checked)}
            />
            Heatmap
          </label>
        </div>

        {loading && (
          <div className="maploading rich">
            <div className="loadrow">
              <span className="spinner" />
              <span>
                {loadingKind === "plan"
                  ? PLAN_STEPS[stepIdx]
                  : "Re-checking weather…"}
              </span>
            </div>
            <div className="loadbar">
              <div className="loadbar-fill" />
            </div>
          </div>
        )}
        {!result && !loading && (
          <div className="maphint">
            Enter an origin and destination, then “Find safest route”.
          </div>
        )}
      </main>
    </div>
  );
}

function worstLabel(milesByRisk) {
  for (let level = 4; level >= 0; level--) {
    if (milesByRisk[String(level)] > 0) return RISK_LEVELS[level].label;
  }
  return "Low";
}
