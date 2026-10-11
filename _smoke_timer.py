#!/usr/bin/env python3
"""Unit test for the timer tool extension (no live pipeline needed)."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext import Manager

events = []
scheduled = []


def host_emit(kind, detail):
    events.append((kind, detail))


def fake_create_task(content, when, speak=None, recurring=None, priority="medium"):
    scheduled.append({"content": content, "time": when, "speak": speak,
                      "priority": priority})
    return len(scheduled)


def main():
    data_root = tempfile.mkdtemp(prefix="sparkext_timer_")
    mgr = Manager(
        root=os.path.join(ROOT, "extensions"),
        data_root=data_root,
        host={"emit": host_emit, "create_task": fake_create_task},
        emit=lambda k, d: events.append((k, d)),
    )
    mgr.load_all(force=True)
    ext = mgr.extensions.get("timer")
    assert ext and ext.active, f"timer not active: {ext and ext.error}"
    decls = ext.hooks["tools"](ext.ctx)
    fn = decls[0]["fn"]
    print("tool:", decls[0]["name"], "-", decls[0]["description"][:70], "....")

    cases = [
        ({"duration": "5 minutes", "label": "tea"}, 300),
        ({"duration": "1 hour 30 minutes"}, 5400),
        ({"duration": "90 seconds"}, 90),
        ({"duration": "25m"}, 1500),
        ({"duration": "10"}, 600),
        ({"duration": "1h30m"}, 5400),
    ]
    for args, expect in cases:
        res = fn(args, ext.ctx)
        assert res.get("ok"), f"{args} -> {res}"
        got = res["data"]["duration_seconds"]
        assert got == expect, f"{args} expected {expect} got {got}"
        print(f"  {args} -> {res['data']}")

    # error path
    bad = fn({"duration": "sometime soon"}, ext.ctx)
    assert not bad.get("ok"), bad
    print("  bad input ->", bad)

    # engine got the schedules
    assert len(scheduled) == len(cases), scheduled
    assert scheduled[0]["speak"].startswith("Hey Ethan")
    assert scheduled[0]["priority"] == "high"
    print("\nscheduled:", len(scheduled), "tasks e.g.", scheduled[0])
    print("\nOK: timer contract verified")


if __name__ == "__main__":
    main()
