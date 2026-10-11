"""Clock & Time — pure tool extension.

Exposes ONE tool: `clock`. It returns the current time/date as DATA. It does NOT
speak and does NOT consume the turn — the watcher routes to it, the result is
handed to OpenClaw, and OpenClaw phrases the spoken reply (TTS reads that).

Contract: a tool returns a dict. `data` is the structured result; the agent sees
it and writes the words.
"""
import time


def setup(ctx):
    ctx.log("clock ready")

    def do_clock(args, ctx):
        what = (args.get("what") or "time").strip().lower()
        if what in ("date", "day", "today"):
            return {"ok": True, "data": {
                "date": ctx.now("%A, %B %-d, %Y"),
                "iso": ctx.now("%Y-%m-%d"),
            }}
        if what in ("both", "datetime", "full"):
            return {"ok": True, "data": {
                "time": ctx.now("%H:%M"),
                "date": ctx.now("%A, %B %-d, %Y"),
            }}
        return {"ok": True, "data": {
            "time": ctx.now("%H:%M"),
            "timezone": time.strftime("%Z"),
        }}

    def tools(ctx):
        return [{
            "name": "clock",
            "description": "Get the current local time and/or date.",
            "args": {"what": {"type": "enum", "required": False,
                              "values": ["time", "date", "both"]}},
            "fn": do_clock,
        }]

    return {"tools": tools}
