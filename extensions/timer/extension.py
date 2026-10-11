"""Timer — pure tool extension.

Exposes ONE tool: `timer`. It schedules a countdown by dropping a reminder into
the task engine (via `ctx.create_task`), then returns RESULT DATA. It does NOT
speak and does NOT consume the turn — the watcher routes to it, OpenClaw phrases
the spoken confirmation, and the task engine speaks the alert when the countdown
ends (TTS reads the `speak` field).

The MODEL interprets language and fills the clean machine args:
  - `duration`: the raw countdown phrase ("5 minutes", "1h30m", "90 seconds")
  - `label`:    what the timer is for (optional)
This tool stays dumb: it parses the phrase to seconds deterministically (offline,
no model), converts it to an absolute local time, and hands it to the engine.

Contract: a tool returns a dict. `data` is the structured result; the agent sees
it and writes the words.
"""
import re

# one token like "1h30m", "90 seconds", "2 hours and 15 minutes", "5min"
_NUM = r"(\d+(?:\.\d+)?)"
_UNIT_MAP = [
    (r"h|hr|hrs|hour|hours", 3600.0),
    (r"m|min|mins|minute|minutes", 60.0),
    (r"s|sec|secs|second|seconds", 1.0),
]
_UNIT_RE = re.compile(r"%s\s*(%s)" % (_NUM, "|".join(u for u, _ in _UNIT_MAP)))


def _parse_seconds(text):
    """Parse a natural-ish duration phrase into seconds. Returns (secs, unit_hits)
    or (None, None) if nothing parseable was found. Deterministic, no model."""
    s = (text or "").strip().lower()
    if not s:
        return None, None
    if s in ("a minute", "one minute"):
        s = "1 minute"
    if s in ("a second", "one second"):
        s = "1 second"
    if s in ("an hour", "one hour"):
        s = "1 hour"
    total = 0.0
    hits = 0
    for m in _UNIT_RE.finditer(s):
        val = float(m.group(1))
        unit = m.group(2)
        for pat, mult in _UNIT_MAP:
            if re.fullmatch(pat, unit):
                total += val * mult
                hits += 1
                break
    if hits == 0:
        # bare number -> treat as minutes ("timer 5" -> 5 minutes)
        bare = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", s)
        if bare:
            return float(bare.group(1)) * 60.0, 1
        return None, None
    return total, hits


def _fmt_seconds(secs):
    secs = int(round(secs))
    if secs < 60:
        return f"{secs} second" + ("s" if secs != 1 else "")
    mins, rem = divmod(secs, 60)
    if rem == 0:
        if mins < 60:
            return f"{mins} minute" + ("s" if mins != 1 else "")
        hrs, m = divmod(mins, 60)
        return (f"{hrs} hour" + ("s" if hrs != 1 else "")
                + (f" {m} minutes" if m else ""))
    return f"{mins} minute" + ("s" if mins != 1 else "") + f" {rem} seconds"


def _add_seconds(base_fmt, secs):
    """Absolute local '%Y-%m-%d %H:%M' `secs` from now, using ctx's clock."""
    import time as _t
    when = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(_t.time() + secs))
    return when


def setup(ctx):
    ctx.log("timer ready")

    def do_timer(args, ctx):
        phrase = (args.get("duration") or "").strip()
        if not phrase:
            return {"ok": False, "error": "no duration given"}
        secs, hits = _parse_seconds(phrase)
        if not secs or secs <= 0:
            return {"ok": False,
                    "error": f"couldn't read a duration from {phrase!r}"}
        label = (args.get("label") or "").strip()
        when = _add_seconds(ctx.now("%Y-%m-%d %H:%M"), secs)
        human = _fmt_seconds(secs)
        if label:
            content = f"timer: {label}"
            speak = f"Hey Ethan, your {human} timer for {label} is done."
        else:
            content = f"timer {human}"
            speak = f"Hey Ethan, your {human} timer is up."

        created = ctx.create_task(content, when, speak=speak,
                                  priority="high")
        # ctx.create_task returns a truthy handle when the engine accepted it,
        # None when the host has no task hook wired (still a valid result).
        return {"ok": True, "data": {
            "duration_seconds": int(round(secs)),
            "duration": human,
            "label": label or None,
            "fires_at": when,
            "scheduled": created is not None,
        }}

    def tools(ctx):
        return [{
            "name": "timer",
            "description": ("Start a countdown timer that speaks an alert when it "
                            "ends. `duration` is a countdown phrase like '5 "
                            "minutes', '90 seconds', '1 hour 30 minutes', or "
                            "'25m'. `label` names what the timer is for "
                            "(optional)."),
            "args": {
                "duration": {"type": "string", "required": True},
                "label": {"type": "string", "required": False},
            },
            "fn": do_timer,
        }]

    return {"tools": tools}
