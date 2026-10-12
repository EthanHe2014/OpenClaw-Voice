"""Weather — pure tool extension.

Exposes ONE tool: `weather`. It fetches the CURRENT weather for a city and
returns it as RESULT DATA. It does NOT speak and does NOT consume the turn — the
watcher routes to it, OpenClaw phrases the spoken reply (TTS reads that).

The MODEL interprets language and fills the clean machine arg:
  - `city`: a place name, e.g. "Beijing", "Shanghai", "San Francisco"

This tool stays dumb: it geocodes the place, fetches the current conditions, and
maps the WMO weather code to a short English phrase. It is offline-safe: any
network/parse error returns `{ok: false}` and the pipeline continues unchanged.

Data source: Open-Meteo (https://open-meteo.com) — free, no API key, no signup.
We call it with the standard-library `urllib` (no third-party deps).

Contract: a tool returns a dict. `data` is the structured result; the agent sees
it and writes the words.
"""
import json
import time
import urllib.parse
import urllib.request

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes -> short English phrase.
WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "light rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "moderate snow", 75: "heavy snow",
    77: "snow grains",
    80: "light rain showers", 81: "moderate rain showers",
    82: "violent rain showers",
    85: "light snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with light hail",
    99: "thunderstorm with heavy hail",
}


def _get(url, params, timeout):
    """GET JSON via stdlib urllib. Returns the parsed dict or raises."""
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url + "?" + qs, headers={"User-Agent": "Spark-Weather/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _code_text(code):
    return WMO.get(code, "unknown conditions")


def setup(ctx):
    ctx.log("weather ready")

    cfg = ctx.config or {}
    try:
        timeout = float(cfg.get("timeout", 6))
    except (TypeError, ValueError):
        timeout = 6.0
    geo_ttl = int(cfg.get("geocode_ttl", 86400))
    fc_ttl = int(cfg.get("forecast_ttl", 600))

    def _cached_place(city):
        """Geocode a city name, cached in per-extension state (24h by default)."""
        key = "geo:" + city.strip().lower()
        hit = ctx.get_state(key)
        now = time.time()
        if hit and isinstance(hit, dict) and now - hit.get("t", 0) < geo_ttl:
            return hit
        data = _get(GEOCODE_URL,
                    {"name": city, "count": 1, "language": "en",
                     "format": "json"}, timeout)
        results = data.get("results") or []
        if not results:
            return None
        r = results[0]
        place = {
            "name": r.get("name") or city,
            "country": r.get("country") or "",
            "admin": r.get("admin1") or "",
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
            "t": now,
        }
        # Keep state small: only remember the last handful of places.
        ctx.set_state(key, place)
        return place

    def do_weather(args, ctx):
        city = (args.get("city") or "").strip()
        if not city:
            return {"ok": False, "error": "no city given"}

        try:
            place = _cached_place(city)
        except Exception as e:
            return {"ok": False, "error": f"geocode failed: {type(e).__name__}"}
        if not place:
            return {"ok": False, "error": f"couldn't find a place called {city!r}"}

        lat, lon = place.get("latitude"), place.get("longitude")
        if lat is None or lon is None:
            return {"ok": False, "error": f"no coordinates for {city!r}"}

        fkey = f"fc:{lat:.3f},{lon:.3f}"
        hit = ctx.get_state(fkey)
        now = time.time()
        if hit and isinstance(hit, dict) and now - hit.get("t", 0) < fc_ttl:
            cur = hit.get("current") or {}
        else:
            try:
                data = _get(FORECAST_URL, {
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,"
                               "apparent_temperature,weather_code,wind_speed_10m",
                    "temperature_unit": "celsius",
                    "wind_speed_unit": "kmh",
                    "timezone": "auto",
                }, timeout)
            except Exception as e:
                return {"ok": False,
                        "error": f"forecast failed: {type(e).__name__}"}
            cur = data.get("current") or {}
            ctx.set_state(fkey, {"current": cur, "t": now})

        code = cur.get("weather_code")
        temp = cur.get("temperature_2m")
        feels = cur.get("apparent_temperature")
        wind = cur.get("wind_speed_10m")
        humidity = cur.get("relative_humidity_2m")
        if temp is None:
            return {"ok": False, "error": "no temperature in response"}

        where = place["name"]
        if place.get("admin") and place["admin"] != place["name"]:
            where += ", " + place["admin"]
        if place.get("country"):
            where += ", " + place["country"]

        return {"ok": True, "data": {
            "city": place["name"],
            "location": where,
            "temperature_c": round(float(temp)),
            "feels_like_c": (round(float(feels))
                             if feels is not None else None),
            "conditions": _code_text(code),
            "weather_code": code,
            "humidity_pct": (round(float(humidity))
                             if humidity is not None else None),
            "wind_kmh": (round(float(wind)) if wind is not None else None),
            "source": "open-meteo",
        }}

    def tools(ctx):
        return [{
            "name": "weather",
            "description": ("Get the current weather for a city. `city` is a place "
                            "name like 'Beijing', 'Shanghai', or 'San Francisco'."),
            "args": {
                "city": {"type": "string", "required": True},
            },
            "fn": do_weather,
        }]

    return {"tools": tools}
