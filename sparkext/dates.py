"""Deterministic date/time phrase resolver for `date` args.

The MODEL interprets language and passes a literal phrase (e.g. "tomorrow at
9am", "in 20 minutes", "3pm", "monday afternoon"); the RUNNER resolves a skill's
declared `date` arg to an ABSOLUTE local value before the skill is called, so
the tool stays dumb (it never parses natural language).

Offline, stdlib only, deterministic (injectable `now` for tests) and it never
raises into the pipeline: an unparseable phrase returns `None`, which the
registry reports as `invalid_args`.

Resolution modes (the arg spec may set `"mode"`):
  - "datetime" (default) -> "YYYY-MM-DD HH:MM"
  - "date"               -> "YYYY-MM-DD"
  - "time"               -> "HH:MM"

Rules:
  - Relative ("in 20 minutes", "in 1h30m"): resolved from now.
  - A bare hour with no am/pm is guessed by era (1-7 -> pm, 8-11 -> am,
    12 -> noon-ish), matching the reminder/timer tools.
  - A weekday ("monday") means the NEXT occurrence (today -> +7 days).
  - A time-only phrase that already passed today rolls to tomorrow.
  - If only a day is given (no clock time), the time defaults to 00:00.
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

_CLOCK_RE = re.compile(r"(?:\bat\b\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3,
    "thurs": 3, "fri": 4, "sat": 5, "sun": 6,
}

_MODES = {"datetime": "%Y-%m-%d %H:%M", "date": "%Y-%m-%d", "time": "%H:%M"}


def _parse_relative(s):
    """Seconds from 'in N minutes'/'in 1h30m', or None. Bare 'in 5' = 5 min."""
    m = re.match(r"in\s+(.+)$", s)
    if not m:
        return None
    body = m.group(1).strip()
    for word, repl in (("a minute", "1 minute"), ("one minute", "1 minute"),
                       ("a second", "1 second"), ("one second", "1 second"),
                       ("an hour", "1 hour"), ("one hour", "1 hour")):
        if body == word:
            body = repl
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
        return float(bare.group(1)) * 60.0     # bare number -> minutes
    return None


def _parse_clock(s):
    """Return (hour, minute) 0-23/0-59, or None. Ambiguous bare hours guessed."""
    if "noon" in s:
        return (12, 0)
    if "midnight" in s:
        return (0, 0)
    m = _CLOCK_RE.search(s)
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
    elif 1 <= h <= 7:
        h += 12          # ambiguous bare hour -> pm
    return (h, mi)


def _day_offset(s, cur_wd):
    """(offset_days, explicit) from day words / weekday / ISO date.
    `explicit` is True when the phrase named a specific day."""
    if "day after tomorrow" in s:
        return 2, True
    if "tomorrow" in s:
        return 1, True
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening|night))\b", s):
        return 0, True
    for name, wd in _WEEKDAYS.items():
        if re.search(r"\b%s\b" % re.escape(name), s):
            off = (wd - cur_wd) % 7
            return (off or 7), True
    return 0, False


def _resolve_struct(phrase, now=None):
    """Return (epoch, has_time, has_day) for a phrase, or None."""
    now = time.time() if now is None else now
    s = (phrase or "").strip().lower()
    if not s:
        return None
    lt = time.localtime(now)

    rel = _parse_relative(s)
    if rel is not None:
        if rel < 0:
            return None
        return now + rel, True, True

    clock = _parse_clock(s)

    iso = _ISO_RE.search(s)
    if iso:
        y, mo, d = (int(x) for x in iso.groups())
        if clock:
            h, mi = clock
        else:
            h = mi = 0
        try:
            return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1)), \
                bool(clock), True
        except (ValueError, OverflowError):
            return None

    off, explicit = _day_offset(s, lt.tm_wday)

    if clock is None:
        if not explicit:
            return None
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + off, 0, 0, 0,
                            0, 0, -1)), False, True

    h, mi = clock
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + off, h, mi, 0,
                        0, 0, -1))
    # A time-only phrase already in the past rolls to tomorrow.
    if not explicit and base <= now:
        base += 86400
    return base, True, explicit


def resolve(phrase, mode="datetime", now=None):
    """Resolve a date/time phrase to an absolute local string, or None.

    `mode` selects the output granularity (see module docstring)."""
    st = _resolve_struct(phrase, now)
    if st is None:
        return None
    epoch = st[0]
    fmt = _MODES.get((mode or "datetime").lower(), _MODES["datetime"])
    return time.strftime(fmt, time.localtime(epoch))


def resolve_struct(phrase, now=None):
    """Public structured form: {epoch, has_time, has_day} or None."""
    st = _resolve_struct(phrase, now)
    if st is None:
        return None
    return {"epoch": st[0], "has_time": st[1], "has_day": st[2]}
