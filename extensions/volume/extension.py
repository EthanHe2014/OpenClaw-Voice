"""System Volume — pure tool extension.

Exposes ONE tool: `sysvolume`. It sets, raises, lowers, or mutes the macOS
system output volume via `osascript`, then returns RESULT DATA. It does NOT
speak and does NOT consume the turn — the watcher routes to it, OpenClaw phrases
the spoken reply (TTS reads that).

The MODEL interprets language and fills the clean machine args:
  - `command`: set | up | down | mute | unmute | get
  - `level`:   target 0-100 (for `set`); a step size (for up/down, default 10)
This tool stays dumb: it just runs osascript. Deterministic, fast, offline.

Contract: a tool returns a dict. `data` is the structured result; the agent sees
it and writes the words.
"""
import re
import subprocess

_TIMEOUT = 6


def _run(script):
    """Run an AppleScript; return (ok, stdout-or-error). Never raises."""
    try:
        r = subprocess.run(["/usr/bin/osascript", "-e", script],
                           capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "osascript timed out"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    return r.returncode == 0, (out if r.returncode == 0 else (err or out))


def _clamp(v):
    return max(0, min(100, int(round(v))))


def _get_state():
    """Return (ok, {'volume': int, 'muted': bool})."""
    ok, out = _run("get volume settings")
    if not ok:
        return False, out
    vol_m = re.search(r"output volume:(\d+)", out)
    mute_m = re.search(r"output muted:(true|false)", out)
    return True, {
        "volume": int(vol_m.group(1)) if vol_m else None,
        "muted": (mute_m.group(1) == "true") if mute_m else None,
    }


def setup(ctx):
    ctx.log("volume ready")

    def do_volume(args, ctx):
        cmd = (args.get("command") or "").strip().lower()
        raw = args.get("level")

        if cmd in ("get", "status", "current"):
            ok, st = _get_state()
            if not ok:
                return {"ok": False, "error": f"couldn't read volume: {st}"}
            return {"ok": True, "data": {"command": "get", **st}}

        if cmd == "set":
            try:
                lvl = _clamp(float(raw))
            except (TypeError, ValueError):
                return {"ok": False, "error": f"set needs a level 0-100, got {raw!r}"}
            ok, out = _run(f"set volume output volume {lvl}")
            if not ok:
                return {"ok": False, "error": f"couldn't set volume: {out}"}
            return {"ok": True, "data": {"command": "set", "volume": lvl}}

        if cmd in ("up", "down", "raise", "lower", "louder", "quieter"):
            step = 10
            if raw not in (None, ""):
                try:
                    step = abs(int(round(float(raw))))
                except (TypeError, ValueError):
                    step = 10
            ok, st = _get_state()
            if not ok:
                return {"ok": False, "error": f"couldn't read volume: {st}"}
            base = st.get("volume") or 0
            delta = -step if cmd in ("down", "lower", "quieter") else step
            lvl = _clamp(base + delta)
            ok, out = _run(f"set volume output volume {lvl}")
            if not ok:
                return {"ok": False, "error": f"couldn't set volume: {out}"}
            # raising the volume should un-mute so the change is actually heard
            if delta > 0 and st.get("muted"):
                _run("set volume without output muted")
            return {"ok": True, "data": {"command": cmd, "from": base,
                                         "volume": lvl, "step": step}}

        if cmd in ("mute", "silence"):
            ok, out = _run("set volume with output muted")
            if not ok:
                return {"ok": False, "error": f"couldn't mute: {out}"}
            return {"ok": True, "data": {"command": "mute", "muted": True}}

        if cmd in ("unmute", "unsilence"):
            ok, out = _run("set volume without output muted")
            if not ok:
                return {"ok": False, "error": f"couldn't unmute: {out}"}
            return {"ok": True, "data": {"command": "unmute", "muted": False}}

        return {"ok": False, "error": f"unknown volume command: {cmd!r}"}

    def tools(ctx):
        return [{
            "name": "sysvolume",
            "description": ("Control the Mac's system output volume. command='set' "
                            "with level 0-100, 'up'/'down' (optional level = step, "
                            "default 10), 'mute', 'unmute', or 'get' to read the "
                            "current volume and mute state."),
            "args": {
                "command": {"type": "enum", "required": True,
                            "values": ["set", "up", "down", "mute", "unmute", "get"]},
                "level": {"type": "number", "required": False},
            },
            "fn": do_volume,
        }]

    return {"tools": tools}
