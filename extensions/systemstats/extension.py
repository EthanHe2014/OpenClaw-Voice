"""System Stats — pure tool extension.

Exposes ONE tool: `stats`. It reports disk, battery, and memory stats for the Mac
and returns RESULT DATA. It does NOT speak and does NOT consume the turn — the
watcher routes to it, then OpenClaw phrases the spoken reply (TTS reads that).

The MODEL interprets language and fills `what` (disk/battery/mem/all). This tool
stays dumb: it runs standard macOS tools (`df`, `pmset`, `vm_stat`, `sysctl`) and
parses their output deterministically. Fully offline, no credential, fast. Only
the `log` scope is used.
"""
import os
import re
import subprocess

_TIMEOUT = 5


def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
        return r.returncode == 0, (r.stdout or "")
    except Exception:
        return False, ""


def _gb(nbytes):
    return round(nbytes / (1024 ** 3), 1)


def _disk():
    """Root volume usage from `df -k /`."""
    ok, out = _run(["/bin/df", "-k", "/"])
    if not ok:
        return None
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    parts = lines[-1].split()
    # filesystem, 1024-blocks, used, avail, capacity%, mounted
    try:
        total = int(parts[1]) * 1024
        used = int(parts[2]) * 1024
        free = int(parts[3]) * 1024
        pct = int((parts[4] or "0%").rstrip("%"))
    except (ValueError, IndexError):
        return None
    return {"total_gb": _gb(total), "used_gb": _gb(used),
            "free_gb": _gb(free), "used_percent": pct}


def _battery():
    """Battery percent + power source from `pmset -g batt`.

    Returns a dict; `present` is False on desktops with no battery.
    """
    ok, out = _run(["/usr/bin/pmset", "-g", "batt"])
    if not ok:
        return {"present": None, "error": "pmset unavailable"}
    info = {"present": False, "source": None,
            "percent": None, "charging": None}
    if "AC Power" in out:
        info["source"] = "ac"
    elif "Battery Power" in out:
        info["source"] = "battery"
    m = re.search(r"(\d+)%", out)
    if m:
        info["present"] = True
        info["percent"] = int(m.group(1))
        low = out.lower()
        if "charging" in low and "discharging" not in low:
            info["charging"] = True
        elif "discharging" in low:
            info["charging"] = False
        elif "charged" in low:
            info["charging"] = True
    return info


def _mem():
    """Memory totals from `sysctl hw.memsize` + `vm_stat` (16K pages on Apple)."""
    ok, out = _run(["/usr/sbin/sysctl", "-n", "hw.memsize"])
    if not ok or not out.strip().isdigit():
        return None
    total = int(out.strip())
    ok, vm = _run(["/usr/bin/vm_stat"])
    if not ok:
        return {"total_gb": _gb(total)}
    pm = re.search(r"page size of (\d+) bytes", vm)
    page = int(pm.group(1)) if pm else 4096

    def pages(label):
        m = re.search(label + r":\s+(\d+)", vm)
        return int(m.group(1)) if m else 0

    free = pages("Pages free") * page
    active = pages("Pages active") * page
    wired = pages("Pages wired down") * page
    compressed = pages("Pages occupied by compressor") * page
    used = active + wired + compressed
    used_pct = round(100.0 * used / total) if total else 0
    return {"total_gb": _gb(total), "free_gb": _gb(free),
            "used_gb": _gb(used), "used_percent": used_pct}


def setup(ctx):
    ctx.log("systemstats ready")

    def _gather(what):
        out = {}
        if what in ("disk", "all"):
            out["disk"] = _disk()
        if what in ("battery", "batt", "power", "all"):
            out["battery"] = _battery()
        if what in ("mem", "memory", "ram", "all"):
            out["memory"] = _mem()
        return out

    def do_stats(args, ctx):
        what = (args.get("what") or "all").strip().lower()
        if what not in ("disk", "battery", "batt", "power", "mem", "memory",
                        "ram", "all"):
            return {"ok": False,
                    "error": f"unknown stats target: {what!r} "
                             "(use disk|battery|mem|all)"}
        key = {"batt": "battery", "power": "battery",
               "memory": "mem", "ram": "mem"}.get(what, what)
        out = _gather(what)
        if all(v is None for v in out.values()):
            return {"ok": False, "error": "couldn't read any system stats"}
        return {"ok": True, "data": {"what": key, "stats": out}}

    def tools(ctx):
        return [{
            "name": "stats",
            "description": ("Report system stats for this Mac (offline). what='disk' "
                            "(root volume usage), 'battery' (charge + power source), "
                            "'mem' (memory usage), or 'all'."),
            "args": {
                "what": {"type": "enum", "required": True,
                         "values": ["disk", "battery", "mem", "all"]},
            },
            "fn": do_stats,
        }]

    return {"tools": tools}
