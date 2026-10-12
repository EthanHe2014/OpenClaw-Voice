#!/usr/bin/env python3
"""Unit test for the dashboard route-log view (read_routes in spark_web).

No live pipeline needed: import the module (it has a __main__ guard) and call
read_routes() against the real voice_routes.jsonl, then against a temp file with
a crafted ok/fail mix.
"""
import os
import sys
import json
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import spark_web  # noqa: E402  (heavy import, but __main__-guarded)


def test_real_log():
    routes, stats = spark_web.read_routes()
    assert isinstance(routes, list) and isinstance(stats, dict), (routes, stats)
    assert set(("total", "ok", "fail", "bad_rate")) <= set(stats), stats
    for r in routes:
        assert set(("isa", "text", "intent", "actions", "failures", "ok")) <= set(r), r
        assert isinstance(r["ok"], bool)
    print("real log ->", stats, "| newest:", routes[0]["text"] if routes else "(none)")


def test_synthetic_mix():
    sample = [
        {"t": 1, "isa": "2026-10-12 10:00:00", "text": "what time is it",
         "intent": "skill", "actions": ["clock"], "handled": ["clock"], "failures": []},
        {"t": 2, "isa": "2026-10-12 10:01:00", "text": "bad thing",
         "intent": "skill", "actions": ["weather"], "handled": [], "failures": ["weather"]},
        {"t": 3, "isa": "2026-10-12 10:02:00", "text": "just chatting",
         "intent": "chat", "actions": [], "handled": [], "failures": []},
    ]
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in sample:
            f.write(json.dumps(r) + "\n")
        f.write("not json at all\n")
    old = spark_web.ROOT
    try:
        spark_web.ROOT = os.path.dirname(path)
        # read_routes joins ROOT with the fixed filename, so point it at a dir
        target = os.path.join(os.path.dirname(path), "voice_routes.jsonl")
        os.replace(path, target)
        routes, stats = spark_web.read_routes()
    finally:
        spark_web.ROOT = old
    assert stats["total"] == 3, stats
    assert stats["ok"] == 1, stats          # only the clock route is ok
    assert stats["fail"] == 1, stats        # weather failed
    assert stats["bad_rate"] == round(100.0 * 1 / 3, 1), stats
    assert routes[0]["text"] == "just chatting"  # newest first
    print("synthetic ->", stats, "| order:", [r["text"] for r in routes])
    os.remove(target)


if __name__ == "__main__":
    test_real_log()
    test_synthetic_mix()
    print("\nOK: route-log view verified")
