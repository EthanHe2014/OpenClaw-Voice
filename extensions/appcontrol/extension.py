"""App Control — pure tool extension.

Exposes ONE tool: `app`. It opens, quits, or focuses a macOS application via
`osascript`, then returns RESULT DATA. It does NOT speak and does NOT consume the
turn — the watcher routes to it, OpenClaw phrases the spoken reply (TTS reads
that).

The MODEL interprets language and fills the clean machine args:
  - `command`: open | quit | focus
  - `name`:    the app's name ("Safari", "NetEase Music", "Notes")
This tool stays dumb: it just runs osascript. Deterministic, fast, offline.

Contract: a tool returns a dict. `data` is the structured result; the agent sees
it and writes the words.
"""
import subprocess

_TIMEOUT = 8


def _run(script):
    """Run an AppleScript; return (ok, stdout/stderr). Never raises."""
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


def _esc(name):
    """Escape a name for safe interpolation into an AppleScript string."""
    return (name or "").replace("\\", "\\\\").replace('"', '\\"')


def setup(ctx):
    ctx.log("appcontrol ready")

    def do_app(args, ctx):
        cmd = (args.get("command") or "").strip().lower()
        name = (args.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "no app name given"}
        safe = _esc(name)

        if cmd == "open":
            ok, out = _run(f'tell application "{safe}" to activate')
            if not ok:
                # fall back to `open -a` for apps that aren't scriptable yet
                try:
                    r = subprocess.run(["/usr/bin/open", "-a", name],
                                       capture_output=True, text=True, timeout=_TIMEOUT)
                    ok, out = (r.returncode == 0), ((r.stdout or r.stderr or "").strip())
                except Exception as e:
                    ok, out = False, f"{type(e).__name__}: {e}"
            return ({"ok": True, "data": {"command": "open", "app": name,
                                          "opened": True}} if ok
                    else {"ok": False, "error": f"couldn't open {name}: {out}"})

        if cmd == "quit":
            ok, out = _run(f'tell application "{safe}" to quit')
            return ({"ok": True, "data": {"command": "quit", "app": name,
                                          "quit": True}} if ok
                    else {"ok": False, "error": f"couldn't quit {name}: {out}"})

        if cmd == "focus":
            ok, out = _run(f'tell application "{safe}" to activate')
            return ({"ok": True, "data": {"command": "focus", "app": name,
                                          "focused": True}} if ok
                    else {"ok": False, "error": f"couldn't focus {name}: {out}"})

        if cmd == "running":
            script = ('if application "%s" is running then\n'
                      'return "running"\nelse\nreturn "not running"\nend if' % safe)
            ok, out = _run(script)
            return ({"ok": True, "data": {"command": "running", "app": name,
                                          "running": out == "running"}} if ok
                    else {"ok": False, "error": f"couldn't query {name}: {out}"})

        return {"ok": False, "error": f"unknown app command: {cmd!r}"}

    def tools(ctx):
        return [{
            "name": "app",
            "description": ("Open, quit, or bring a macOS app to the front. "
                            "command='open' launches/activates it, 'quit' closes "
                            "it, 'focus' brings it forward, 'running' reports "
                            "whether it is open. `name` is the app name like "
                            "'Safari' or 'Notes'."),
            "args": {
                "command": {"type": "enum", "required": True,
                            "values": ["open", "quit", "focus", "running"]},
                "name": {"type": "string", "required": True},
            },
            "fn": do_app,
        }]

    return {"tools": tools}
