"""Notes — pure tool extension.

Exposes ONE tool: `note`. It captures a note to a dated file and returns RESULT
DATA. It does NOT speak and does NOT consume the turn — the watcher routes to it,
then OpenClaw phrases the spoken reply (TTS reads that).

The MODEL interprets language and fills `command` (add/list/read) and `text`.
This tool stays dumb: it just writes/reads text. Notes live inside its own
`ctx.data_dir` (no extra scope needed).
"""
import os
import time


def _path(ctx, day=None):
    day = day or ctx.now("%Y-%m-%d")
    return os.path.join(ctx.data_dir, f"notes-{day}.md")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _lines(path):
    raw = _read(path)
    return [ln for ln in raw.splitlines() if ln.strip()]


def setup(ctx):
    ctx.log("notes ready")

    def do_note(args, ctx):
        cmd = (args.get("command") or "add").strip().lower()
        text = (args.get("text") or "").strip()

        if cmd == "add":
            if not text:
                return {"ok": False, "error": "nothing to save (empty text)"}
            p = _path(ctx)
            os.makedirs(ctx.data_dir, exist_ok=True)
            stamp = ctx.now("%H:%M")
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"- {stamp} {text}\n")
            return {"ok": True, "data": {
                "action": "add", "saved": text, "date": ctx.now("%Y-%m-%d"),
                "count": len(_lines(p))}}

        if cmd == "list":
            p = _path(ctx)
            items = _lines(p)
            return {"ok": True, "data": {
                "action": "list", "date": ctx.now("%Y-%m-%d"),
                "count": len(items), "items": [ln.lstrip("- ").strip() for ln in items]}}

        if cmd == "read":
            p = _path(ctx)
            body = _read(p).strip()
            return {"ok": True, "data": {
                "action": "read", "date": ctx.now("%Y-%m-%d"),
                "text": body or "(no notes for today)"}}

        return {"ok": False, "error": f"unknown note command: {cmd!r}"}

    def tools(ctx):
        return [{
            "name": "note",
            "description": ("Save or read quick notes. command='add' (save `text` "
                            "to today's notes), 'list' (list today's notes), or "
                            "'read' (read today's notes back)."),
            "args": {
                "command": {"type": "enum", "required": True,
                            "values": ["add", "list", "read"]},
                "text": {"type": "string", "required": False},
            },
            "fn": do_note,
        }]

    return {"tools": tools}
