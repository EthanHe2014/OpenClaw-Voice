"""Clipboard — pure tool extension.

Exposes ONE tool: `clipboard`. It reads or writes the macOS clipboard and
returns RESULT DATA. It does NOT speak and does NOT consume the turn — the
watcher routes to it, then OpenClaw phrases the spoken reply (TTS reads that).

The MODEL interprets language and fills `command` (read/write/clear) and `text`.
This tool stays dumb: it just moves text to/from the system clipboard via the
built-in `pbpaste`/`pbcopy` commands. No scopes beyond `log` are needed.
"""
import subprocess


def _pbpaste():
    return subprocess.run(["pbpaste"], capture_output=True, text=True,
                          timeout=2).stdout


def _pbcopy(text):
    subprocess.run(["pbcopy"], input=text, text=True, timeout=2, check=True)


def setup(ctx):
    ctx.log("clipboard ready")

    def do_clipboard(args, ctx):
        cmd = (args.get("command") or "read").strip().lower()

        if cmd == "read":
            try:
                body = _pbpaste()
            except Exception as e:
                return {"ok": False, "error": f"clipboard read failed: {e}"}
            return {"ok": True, "data": {
                "action": "read", "chars": len(body),
                "text": body if body.strip() else "(clipboard is empty)"}}

        if cmd == "write":
            text = args.get("text") or ""
            if text == "":
                return {"ok": False, "error": "nothing to copy (empty text)"}
            try:
                _pbcopy(text)
            except Exception as e:
                return {"ok": False, "error": f"clipboard write failed: {e}"}
            return {"ok": True, "data": {
                "action": "write", "copied": True, "chars": len(text)}}

        if cmd in ("clear", "empty"):
            try:
                _pbcopy("")
            except Exception as e:
                return {"ok": False, "error": f"clipboard clear failed: {e}"}
            return {"ok": True, "data": {"action": "clear", "cleared": True}}

        return {"ok": False, "error": f"unknown clipboard command: {cmd!r}"}

    def tools(ctx):
        return [{
            "name": "clipboard",
            "description": ("Read or write the macOS clipboard. command='read' "
                            "(return the current clipboard text), 'write' (copy "
                            "`text` to the clipboard), or 'clear' (empty it)."),
            "args": {
                "command": {"type": "enum", "required": True,
                            "values": ["read", "write", "clear"]},
                "text": {"type": "string", "required": False},
            },
            "fn": do_clipboard,
        }]

    return {"tools": tools}
