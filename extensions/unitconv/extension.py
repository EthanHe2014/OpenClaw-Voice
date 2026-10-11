"""Unit conversion — pure tool extension.

Exposes ONE tool: `convert`. It converts a value between units and returns
RESULT DATA. It does NOT speak and does NOT consume the turn — the watcher routes
to it, then OpenClaw phrases the spoken reply (TTS reads that).

The MODEL interprets language and fills the clean machine args:
  - `value`: a number
  - `from` / `to`: unit tokens (see the tables below)
This tool stays dumb: it looks the units up in static tables and does the math.
Offline only (no network, no scope). Currency is intentionally NOT supported
(that needs a live rate API/credential).
"""
import re

# len/mass/volume/time/speed/data/area use a multiplicative factor to a base.
# temperature is special-cased below.
_UNITS = {
    # length (base: meter)
    "m": ("length", 1.0), "meter": ("length", 1.0), "meters": ("length", 1.0),
    "km": ("length", 1000.0), "kilometer": ("length", 1000.0),
    "kilometers": ("length", 1000.0), "kilometre": ("length", 1000.0),
    "cm": ("length", 0.01), "centimeter": ("length", 0.01),
    "centimeters": ("length", 0.01),
    "mm": ("length", 0.001), "millimeter": ("length", 0.001),
    "millimeters": ("length", 0.001),
    "mi": ("length", 1609.344), "mile": ("length", 1609.344),
    "miles": ("length", 1609.344),
    "yd": ("length", 0.9144), "yard": ("length", 0.9144), "yards": ("length", 0.9144),
    "ft": ("length", 0.3048), "foot": ("length", 0.3048), "feet": ("length", 0.3048),
    "in": ("length", 0.0254), "inch": ("length", 0.0254), "inches": ("length", 0.0254),
    # mass (base: kilogram)
    "kg": ("mass", 1.0), "kilogram": ("mass", 1.0), "kilograms": ("mass", 1.0),
    "g": ("mass", 0.001), "gram": ("mass", 0.001), "grams": ("mass", 0.001),
    "mg": ("mass", 1e-6), "milligram": ("mass", 1e-6), "milligrams": ("mass", 1e-6),
    "lb": ("mass", 0.45359237), "lbs": ("mass", 0.45359237),
    "pound": ("mass", 0.45359237), "pounds": ("mass", 0.45359237),
    "oz": ("mass", 0.028349523125), "ounce": ("mass", 0.028349523125),
    "ounces": ("mass", 0.028349523125),
    "ton": ("mass", 1000.0), "tonne": ("mass", 1000.0), "tons": ("mass", 1000.0),
    # volume (base: liter)
    "l": ("volume", 1.0), "liter": ("volume", 1.0), "liters": ("volume", 1.0),
    "litre": ("volume", 1.0), "litres": ("volume", 1.0),
    "ml": ("volume", 0.001), "milliliter": ("volume", 0.001),
    "milliliters": ("volume", 0.001),
    "gal": ("volume", 3.785411784), "gallon": ("volume", 3.785411784),
    "gallons": ("volume", 3.785411784),
    "qt": ("volume", 0.946352946), "quart": ("volume", 0.946352946),
    "quarts": ("volume", 0.946352946),
    "pt": ("volume", 0.473176473), "pint": ("volume", 0.473176473),
    "pints": ("volume", 0.473176473),
    "cup": ("volume", 0.2365882365), "cups": ("volume", 0.2365882365),
    # time (base: second)
    "s": ("time", 1.0), "sec": ("time", 1.0), "second": ("time", 1.0),
    "seconds": ("time", 1.0),
    "min": ("time", 60.0), "minute": ("time", 60.0), "minutes": ("time", 60.0),
    "h": ("time", 3600.0), "hr": ("time", 3600.0), "hour": ("time", 3600.0),
    "hours": ("time", 3600.0),
    "day": ("time", 86400.0), "days": ("time", 86400.0),
    "week": ("time", 604800.0), "weeks": ("time", 604800.0),
    # speed (base: meter/second)
    "mps": ("speed", 1.0), "kmh": ("speed", 1.0 / 3.6), "kph": ("speed", 1.0 / 3.6),
    "mph": ("speed", 0.44704), "knot": ("speed", 0.514444), "knots": ("speed", 0.514444),
    # data (base: byte)
    "b": ("data", 1.0), "byte": ("data", 1.0), "bytes": ("data", 1.0),
    "kb": ("data", 1000.0), "mb": ("data", 1e6), "gb": ("data", 1e9),
    "tb": ("data", 1e12),
    "kib": ("data", 1024.0), "mib": ("data", 1048576.0), "gib": ("data", 1073741824.0),
}

# temperature: canonical names keyed to (offsets+scale handled specially)
_TEMP = {
    "c": "C", "celsius": "C", "centigrade": "C",
    "f": "F", "fahrenheit": "F",
    "k": "K", "kelvin": "K",
}


def _norm(u):
    return re.sub(r"[\s\.]", "", (u or "").strip().lower())


def _to_celsius(v, scale):
    if scale == "C":
        return v
    if scale == "F":
        return (v - 32.0) * 5.0 / 9.0
    return v - 273.15  # K


def _from_celsius(c, scale):
    if scale == "C":
        return c
    if scale == "F":
        return c * 9.0 / 5.0 + 32.0
    return c + 273.15


def _fmt(v):
    if isinstance(v, float):
        if abs(v) >= 1e15 or (v != 0 and abs(v) < 1e-9):
            return float(f"{v:.6g}")
        r = round(v, 6)
        return int(r) if r == int(r) else r
    return v


def setup(ctx):
    ctx.log("unitconv ready")

    def do_convert(args, ctx):
        raw = args.get("value")
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"value must be a number, got {raw!r}"}
        frm, to = _norm(args.get("from")), _norm(args.get("to"))
        if not frm or not to:
            return {"ok": False, "error": "from and to units are required"}

        # temperature
        if frm in _TEMP or to in _TEMP:
            if frm not in _TEMP or to not in _TEMP:
                return {"ok": False,
                        "error": f"cannot mix temperature {frm!r} with {to!r}"}
            c = _to_celsius(val, _TEMP[frm])
            out = _from_celsius(c, _TEMP[to])
            return {"ok": True, "data": {
                "value": _fmt(val), "from": frm, "to": to,
                "result": _fmt(out), "dimension": "temperature"}}

        a = _UNITS.get(frm)
        b = _UNITS.get(to)
        if a is None:
            return {"ok": False, "error": f"unknown unit {frm!r}"}
        if b is None:
            return {"ok": False, "error": f"unknown unit {to!r}"}
        if a[0] != b[0]:
            return {"ok": False,
                    "error": f"cannot convert {a[0]} to {b[0]} ({frm} -> {to})"}
        out = val * a[1] / b[1]
        return {"ok": True, "data": {
            "value": _fmt(val), "from": frm, "to": to,
            "result": _fmt(out), "dimension": a[0]}}

    def tools(ctx):
        return [{
            "name": "convert",
            "description": ("Convert a value between units. Covers length, mass, "
                            "volume, time, speed, data, and temperature. `from` "
                            "and `to` are unit tokens like 'km','miles','kg','lb',"
                            "'celsius','fahrenheit','gb','mb','minutes'. Offline; "
                            "no currency."),
            "args": {
                "value": {"type": "number", "required": True},
                "from": {"type": "string", "required": True},
                "to": {"type": "string", "required": True},
            },
            "fn": do_convert,
        }]

    return {"tools": tools}
