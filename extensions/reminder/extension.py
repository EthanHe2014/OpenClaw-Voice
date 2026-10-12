"""Reminder — pure tool extension.

Exposes ONE tool: `reminder`. It schedules a reminder by dropping one into the
task engine (via `ctx.create_task`) and returns RESULT DATA. It does NOT speak and
does NOT consume the turn — the watcher routes to it, OpenClaw phrases the spoken
confirmation, and the task engine speaks the reminder when the time arrives (TTS
reads the `speak` field).

The MODEL interprets language and fills the clean machine args:
  - `content`: what to be reminded about (short phrase)
  - `when`:    a time phrase ("in 20 minutes", "tomorrow at 9am", "3:30pm", "at 4")
This tool stays dumb: it parses the phrase to an absolute local time
deterministically (offline, no model), then hands it to the engine.

Contract: a tool returns a dict. `data` is the structured result; the agent sees
it and writes the words.
"""
import re
import time

_NUM = r"(\d+(?:\.\d+)?)"
_UNIT_MAP = [
    (r"h|hr|hrs|hour|hours", 3600.0),
    (r"m|min|mins|minute|minutes", 60.0),
    (r"s|sec|secs|second|seconds", 1.0),
]
_UNIT_RE = re.compile(r"%s\s*(%s)" % (_NUM, "|".join(u for u, _ in _UNIT_MAP)))

# clock time: optional "at", H[:MM] with optional am/pm
_TIME_RE = re.compile(
    r"(?:\bat\b\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b")

_MERIDIEM_RE = re.compile(r"\b(am|pm)\b")


def _parse_relative(text):
    """Return seconds from 'in N minutes'/'in 1h30m', or None. Bare 'in 5' = 5 min."""
    s = text.strip().lower()
    m = re.match(r"in\s+(.+)$", s)
    if not m:
        return None
    body = m.group(1).strip()
    if body in ("a minute", "one minute"):
        body = "1 minute"
    if body in ("a second", "one second"):
        body = "1 second"
    if body in ("an hour", "one hour"):
        body = "1 hour"
    total = 0.0
    hits = 0
    for mm in _UNIT_RE.finditer(body):
        val = float(mm.group(1))
        unit = mm.group(2)
        for pat, mult in _UNIT_MAP:
            if re.fullmatch(pat, unit):
                total += val * mult
                hits += 1
                break
    if hits:
        return total
    bare = re.fullmatch(r"(\d+(?:\.\d+)?)", body)
    if bare:
        return float(bare.group(1)) * 60.0  # bare number -> minutes
    return None


def _parse_day_offset(text):
    s = text.strip().lower()
    if "tomorrow" in s:
        return 1
    if "day after tomorrow" in s:
        return 2
    return 0  # today/tonight/now/unspecified


def _resolve_clock(text):
    """Return (hour, minute) 0-23/0-59 from a clock phrase, or None.

    Handles 'noon', 'midnight', '3pm', '3:30 pm', '15:00', 'at 4'. Ambiguous
    bare hours default by era: 1-7 -> pm, 8-11 -> am, 12 -> 12.
    """
    s = text.strip().lower()
    if "noon" in s:
        return (12, 0)
    if "midnight" in s:
        return (0, 0)
    m = _TIME_RE.search(s)
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2)) if m.group(2) else 0
    ap = m.group(3)
    if mi > 59 or h > 23:
        return None
    if ap == "pm":
        if h < 12:
            h += 12
    elif ap == "am":
        if h == 12:
            h = 0
    else:
        # ambiguous bare hour: guess the era
        if 1 <= h <= 7:
            h += 12
        elif h == 12:
            h = 12
    return (h, mi)


def setup(ctx):
    ctx.log("reminder ready")

    def do_reminder(args, ctx):
        content = (args.get("content") or "").strip()
        when_phrase = (args.get("when") or "").strip()
        if not content:
            return {"ok": False, "error": "no reminder content given"}
        if not when_phrase:
            return {"ok": False, "error": "no time given for the reminder"}

        now = time.time()
        low = when_phrase.lower()

        # 1) relative: "in 20 minutes"
        rel = _parse_relative(low)
        explicit_day = any(w in low for w in ("tomorrow", "today", "tonight"))
        if rel is not None:
            if rel <= 0:
                return {"ok": False,
                        "error": f"couldn't read a time from {when_phrase!r}"}
            when = time.strftime("%Y-%m-%d %H:%M",
                                 time.localtime(now + rel))
            return self_done(content, when, when_phrase)

        # 2) clock time: "3:30pm", "tomorrow at 9", "at 4"
        clock = _resolve_clock(low)
        if clock is None:
            return {"ok": False,
                    "error": f"couldn't read a time from {when_phrase!r}"}
        day = _parse_day_offset(low)
        h, mi = clock
        lt = time.localtime(now)
        base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                            h, mi, 0, 0, 0, -1))
        if day:
            base += day * 86400
        # if only a clock time was given and it already passed, roll to tomorrow
        elif base <= now:
            base += 86400
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(base))
        return self_done(content, when, when_phrase)

    def self_done(content, when, when_phrase):
        speak = f"Hey Ethan, reminder: {content}."
        created = ctx.create_task(content, when, speak=speak,
                                  priority="medium")
        return {"ok": True, "data": {
            "content": content,
            "when": when,
            "requested": when_phrase,
            "speak": speak,
            "scheduled": created is not None,
        }}

    def tools(ctx):
        return [{
            "name": "reminder",
            "description": ("Set a spoken reminder. `content` is what to be "
                            "reminded about; `when` is a time phrase like 'in 20 "
                            "minutes', 'tomorrow at 9am', '3:30pm', or 'at 4'. The "
                            "reminder is spoken aloud at that time."),
            "args": {
                "content": {"type": "string", "required": True},
                "when": {"type": "string", "required": True},
            },
            "fn": do_reminder,
        }]

    return {"tools": tools}
