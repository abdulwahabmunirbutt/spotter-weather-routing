// Thin API layer — base URL via VITE_API_BASE (defaults to same-origin /api).
const BASE = import.meta.env.VITE_API_BASE || "/api";

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `API ${res.status}`);
  return data;
}

export const geocode = (q) => post("/geocode", { q });

export const planRoute = ({ origin, destination, departure_iso, load_lb, sample_miles }) =>
  post("/plan", { origin, destination, departure_iso, load_lb, sample_miles });

export const rescoreWeather = ({ checkpoints, load_lb }) =>
  post("/weather", { checkpoints, load_lb });
