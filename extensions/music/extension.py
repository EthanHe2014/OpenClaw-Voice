"""Music — pure tool extension.

Exposes ONE tool: `music`. It runs the host helpers (`~/bin/ncplay`,
`~/bin/ncduck`) and returns RESULT DATA. It does NOT speak and does NOT consume
the turn — OpenClaw phrases the reply (TTS reads that).

The MODEL is the interpreter: it fills `command` (play/stop/volume) and, for
volume, a numeric `level`. This tool stays dumb.
"""
import os
import re
import subprocess

PLAY = os.path.expanduser("~/bin/ncplay")
DUCK = os.path.expanduser("~/bin/ncduck")


def _run(cmd, timeout=15):
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode == 0, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return False, "", str(e)


def setup(ctx):
    ctx.log("music ready")

    def do_music(args, ctx):
        cmd = (args.get("command") or "").strip().lower()
        target = (args.get("target") or "").strip()

        if cmd in ("play", "resume", "start"):
            ok, out, err = _run([PLAY, target] if target else [PLAY])
            if not ok:
                return {"ok": False, "error": err or "play failed"}
            return {"ok": True, "data": {
                "action": "play", "target": target or "liked_songs"}}

        if cmd in ("stop", "halt", "pause", "kill"):
            ok, out, err = _run([PLAY, "stop"])
            return {"ok": ok, "data": {"action": "stop"}}

        if cmd in ("volume", "set_volume"):
            v = args.get("level")
            if v is None:
                return {"ok": False, "error": "volume needs a numeric level 0-100"}
            v = max(0, min(100, int(float(v))))
            ok, out, err = _run([DUCK, str(v)])
            return {"ok": ok, "data": {"action": "volume", "level": v}}

        return {"ok": False, "error": f"unknown music command: {cmd!r}"}

    def tools(ctx):
        return [{
            "name": "music",
            "description": ("Control NetEase music. command='play' (optional NetEase "
                            "URL as target, else liked songs), 'stop', or 'volume' "
                            "with numeric level 0-100."),
            "args": {
                "command": {"type": "enum", "required": True,
                            "values": ["play", "stop", "volume"]},
                "target": {"type": "string", "required": False},
                "level": {"type": "number", "required": False},
            },
            "fn": do_music,
        }]

    return {"tools": tools}
