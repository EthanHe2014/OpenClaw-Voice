#!/usr/bin/env python3
"""Unit test for the weather tool extension (no live pipeline needed).

Uses a temporary Manager + a fake host so nothing touches the running loop.
Network is required for the live check; offline the fetch returns ok:false and
the assertions on the error path still hold.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext import Manager

events = []


def main():
    data_root = tempfile.mkdtemp(prefix="sparkext_weather_")
    mgr = Manager(
        root=os.path.join(ROOT, "extensions"),
        data_root=data_root,
        host={"emit": lambda k, d: events.append((k, d))},
        emit=lambda k, d: events.append((k, d)),
    )
    mgr.load_all(force=True)
    ext = mgr.extensions.get("weather")
    assert ext and ext.active, f"weather not active: {ext and ext.error}"
    assert "network" in (ext.scopes or []), ext.scopes
    decls = ext.hooks["tools"](ext.ctx)
    fn = decls[0]["fn"]
    print("tool:", decls[0]["name"], "-", decls[0]["description"][:70], "...")

    # error path: no city / unknown place must be ok:false, never crash
    assert fn({}, ext.ctx).get("ok") is False
    bad = fn({"city": "zzzznotaplace"}, ext.ctx)
    assert bad.get("ok") is False, bad
    print("  no-city ->", fn({}, ext.ctx))
    print("  unknown-place ->", bad)

    # live path (skipped gracefully if offline)
    res = fn({"city": "Beijing"}, ext.ctx)
    if res.get("ok"):
        d = res["data"]
        for k in ("city", "temperature_c", "conditions"):
            assert k in d, (k, d)
        assert isinstance(d["temperature_c"], int), d
        print("  live ->", d)
        # cached second call stays ok
        assert fn({"city": "beijing"}, ext.ctx).get("ok") is True
        print("\nOK: weather contract verified (live)")
    else:
        print("  live -> skipped/offline:", res)
        print("\nOK: weather contract verified (offline error path)")


if __name__ == "__main__":
    main()
