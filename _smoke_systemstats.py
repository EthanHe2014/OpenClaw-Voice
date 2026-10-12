#!/usr/bin/env python3
"""Unit test for the systemstats tool extension (no live pipeline needed)."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext import Manager

events = []


def main():
    data_root = tempfile.mkdtemp(prefix="sparkext_stats_")
    mgr = Manager(
        root=os.path.join(ROOT, "extensions"),
        data_root=data_root,
        host={"emit": lambda k, d: events.append((k, d))},
        emit=lambda k, d: events.append((k, d)),
    )
    mgr.load_all(force=True)
    ext = mgr.extensions.get("systemstats")
    assert ext and ext.active, f"systemstats not active: {ext and ext.error}"
    fn = ext.hooks["tools"](ext.ctx)[0]["fn"]
    print("tool: stats")

    r = fn({"what": "disk"}, ext.ctx)
    assert r["ok"], r
    d = r["data"]["stats"]["disk"]
    assert d and d["total_gb"] > 0 and 0 <= d["used_percent"] <= 100, r
    print("  disk ->", d)

    r = fn({"what": "mem"}, ext.ctx)
    assert r["ok"], r
    m = r["data"]["stats"]["memory"]
    assert m and m["total_gb"] > 0 and 0 <= m["used_percent"] <= 100, r
    print("  mem  ->", m)

    r = fn({"what": "battery"}, ext.ctx)
    assert r["ok"], r
    b = r["data"]["stats"]["battery"]
    assert b is not None, r
    print("  batt ->", b)

    r = fn({"what": "all"}, ext.ctx)
    assert r["ok"], r
    assert set(r["data"]["stats"].keys()) == {"disk", "battery", "memory"}, r
    print("  all  -> keys", sorted(r["data"]["stats"].keys()))

    r = fn({"what": "banana"}, ext.ctx)
    assert not r["ok"], r
    print("  bad target rejected ->", r["error"])

    print("\nOK: systemstats tool verified")


if __name__ == "__main__":
    main()
