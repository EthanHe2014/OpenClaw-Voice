"""Screenshot — pure tool extension.

Exposes ONE tool: `screenshot`. It captures the screen (or the frontmost window)
to a PNG file and returns RESULT DATA. It does NOT speak and does NOT consume the
turn — the watcher routes to it, then OpenClaw phrases the spoken reply (TTS reads
that).

The MODEL interprets language and fills `mode` (screen/window) and an optional
`path`. This tool stays dumb: it just runs macOS `screencapture`. Deterministic,
fast, offline. No scopes beyond `log` are needed: by default files land inside its
own `ctx.data_dir` (which is always writable); an explicit absolute `path` is
honored as-is.
"""
import os
import re
import subprocess

_SCREENCAPTURE = "/usr/sbin/screencapture"
_OSASCRIPT = "/usr/bin/osascript"
_TIMEOUT = 10


def _default_path(ctx):
    name = "shot-" + ctx.now("%Y-%m-%d-%H%M%S") + ".png"
    return os.path.join(ctx.data_dir, name)


def _resolve_path(ctx, raw):
    """Bare names -> data_dir; absolute paths honored as-is."""
    raw = (raw or "").strip()
    if not raw:
        return _default_path(ctx)
    if os.path.isabs(raw):
        return raw
    # make sure it ends in an image extension we asked screencapture for
    if not re.search(r"\.(png|jpg|jpeg|tiff|pdf)$", raw, re.I):
        raw += ".png"
    return os.path.join(ctx.data_dir, raw)


def _front_window_bounds():
    """Return (ok, 'x,y,w,h') for the frontmost window, else (False, reason)."""
    script = ('tell application "System Events" to tell '
              '(first application process whose frontmost is true) '
              'to get {position, size} of front window')
    try:
        r = subprocess.run([_OSASCRIPT, "-e", script],
                           capture_output=True, text=True, timeout=_TIMEOUT)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        return False, (r.stderr or "").strip() or "no front window"
    nums = re.findall(r"-?\d+", r.stdout or "")
    if len(nums) < 4:
        return False, "couldn't read window bounds"
    x, y, w, h = (int(n) for n in nums[:4])
    if w <= 0 or h <= 0:
        return False, "empty window bounds"
    return True, f"{x},{y},{w},{h}"


def setup(ctx):
    ctx.log("screenshot ready")

    def do_screenshot(args, ctx):
        mode = (args.get("mode") or "screen").strip().lower()
        raw_path = args.get("path")

        if mode not in ("screen", "window", "full", "front"):
            return {"ok": False,
                    "error": f"unknown screenshot mode: {mode!r} (use screen|window)"}
        full = mode in ("screen", "full")
        want_window = mode in ("window", "front")

        path = _resolve_path(ctx, raw_path)
        parent = os.path.dirname(path)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except Exception as e:
                return {"ok": False, "error": f"couldn't create folder: {e}"}

        cmd = [_SCREENCAPTURE, "-x"]  # -x = silent (no shutter sound)
        dims = None
        if want_window:
            ok, bounds = _front_window_bounds()
            if not ok:
                return {"ok": False,
                        "error": f"couldn't find a front window: {bounds}"}
            cmd += ["-R", bounds]  # capture just that rect
            m = re.match(r"(\d+),(\d+),(\d+),(\d+)", bounds)
            if m:
                dims = {"x": int(m.group(1)), "y": int(m.group(2)),
                        "w": int(m.group(3)), "h": int(m.group(4))}
        cmd.append(path)

        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=_TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "screencapture timed out"}
        except Exception as e:
            return {"ok": False, "error": f"screencapture failed: {e}"}
        if r.returncode != 0 or not os.path.exists(path):
            err = (r.stderr or "").strip() or "no file written (permission?)"
            return {"ok": False, "error": f"capture failed: {err}"}

        size = os.path.getsize(path)
        data = {
            "action": "screenshot",
            "mode": "window" if want_window else "screen",
            "path": path,
            "filename": os.path.basename(path),
            "bytes": size,
        }
        if dims:
            data["window"] = dims
        return {"ok": True, "data": data}

    def tools(ctx):
        return [{
            "name": "screenshot",
            "description": ("Capture a screenshot to a PNG file. mode='screen' "
                            "(the whole display) or 'window' (the frontmost "
                            "window). Optional `path` names the file; without it "
                            "a timestamped name is used."),
            "args": {
                "mode": {"type": "enum", "required": True,
                         "values": ["screen", "window"]},
                "path": {"type": "string", "required": False},
            },
            "fn": do_screenshot,
        }]

    return {"tools": tools}
