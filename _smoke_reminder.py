#!/usr/bin/env python3
"""Unit test for the reminder tool extension (no live pipeline needed).

Uses a fake host create_task hook so no real reminder is scheduled.
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext import Manager

events = []
scheduled = []


def main():
    data_root = tempfile.mkdtemp(prefix="sparkext_rem_")

    def fake_create_task(content, when, speak=None, recurring=None,
                         priority="medium"):
        scheduled.append({"content": content, "when": when, "speak": speak,
                          "priority": priority})
        return {"id": "t-1"}

    mgr = Manager(
        root=os.path.join(ROOT, "extensions"),
        data_root=data_root,
        host={"emit": lambda k, d: events.append((k, d)),
              "create_task": fake_create_task},
        emit=lambda k, d: events.append((k, d)),
    )
    mgr.load_all(force=True)
    ext = mgr.extensions.get("reminder")
    assert ext and ext.active, f"reminder not active: {ext and ext.error}"
    fn = ext.hooks["tools"](ext.ctx)[0]["fn"]
    print("tool: reminder")

    # relative
    r = fn({"content": "take the pasta off", "when": "in 12 minutes"}, ext.ctx)
    assert r["ok"], r
    assert r["data"]["scheduled"] is True, r
    exp = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + 720))
    assert r["data"]["when"][:16] == exp[:16], (r["data"]["when"], exp)
    print("  relative ->", r["data"]["when"], "|", r["data"]["speak"])

    # explicit pm clock
    r = fn({"content": "call Mom", "when": "3:30pm"}, ext.ctx)
    assert r["ok"], r
    assert r["data"]["when"].endswith("15:30"), r
    print("  clock pm ->", r["data"]["when"])

    # tomorrow at 9am
    r = fn({"content": "school run", "when": "tomorrow at 9am"}, ext.ctx)
    assert r["ok"], r
    assert r["data"]["when"].endswith("09:00"), r
    print("  tomorrow ->", r["data"]["when"])

    # noon
    r = fn({"content": "lunch", "when": "at noon"}, ext.ctx)
    assert r["ok"] and r["data"]["when"].endswith("12:00"), r
    print("  noon ->", r["data"]["when"])

    # bare hour ambiguity: "at 4" -> 16:00
    r = fn({"content": "match", "when": "at 4"}, ext.ctx)
    assert r["ok"] and r["data"]["when"].endswith("16:00"), r
    print("  bare hour ->", r["data"]["when"])

    assert len(scheduled) == 5, scheduled

    # errors
    r = fn({"content": "", "when": "in 5 minutes"}, ext.ctx)
    assert not r["ok"], r
    r = fn({"content": "x", "when": "whenever"}, ext.ctx)
    assert not r["ok"], r
    print("  bad inputs rejected ->", r["error"])

    print("\nOK: reminder tool verified")


if __name__ == "__main__":
    main()
