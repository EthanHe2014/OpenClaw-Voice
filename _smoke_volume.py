#!/usr/bin/env python3
"""Unit test for the volume tool extension (no live pipeline needed).

Touches the real system volume briefly, then restores the original setting.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext import Manager

events = []


def main():
    data_root = tempfile.mkdtemp(prefix="sparkext_vol_")
    mgr = Manager(
        root=os.path.join(ROOT, "extensions"),
        data_root=data_root,
        host={"emit": lambda k, d: events.append((k, d))},
        emit=lambda k, d: events.append((k, d)),
    )
    mgr.load_all(force=True)
    ext = mgr.extensions.get("volume")
    assert ext and ext.active, f"volume not active: {ext and ext.error}"
    fn = ext.hooks["tools"](ext.ctx)[0]["fn"]
    print("tool: sysvolume")

    before = fn({"command": "get"}, ext.ctx)
    assert before.get("ok"), before
    orig_vol = before["data"]["volume"]
    orig_muted = before["data"]["muted"]
    print("  get before ->", before)

    try:
        r = fn({"command": "set", "level": 33}, ext.ctx)
        assert r["data"]["volume"] == 33, r
        print("  set 33 ->", r)

        r = fn({"command": "up", "level": 12}, ext.ctx)
        assert r["data"]["volume"] == 45, r
        print("  up 12 ->", r)

        r = fn({"command": "down", "level": 20}, ext.ctx)
        assert r["data"]["volume"] == 25, r
        print("  down 20 ->", r)

        r = fn({"command": "set", "level": 150}, ext.ctx)
        assert r["data"]["volume"] == 100, r
        print("  set 150 clamps ->", r)

        r = fn({"command": "mute"}, ext.ctx)
        assert r["data"]["muted"] is True, r
        print("  mute ->", r)

        r = fn({"command": "unmute"}, ext.ctx)
        assert r["data"]["muted"] is False, r
        print("  unmute ->", r)

        r = fn({"command": "set", "level": "loud"}, ext.ctx)
        assert not r.get("ok"), r
        print("  set 'loud' ->", r)

        r = fn({"command": "zing"}, ext.ctx)
        assert not r.get("ok") and "unknown" in r["error"], r
        print("  bad cmd ->", r)
    finally:
        fn({"command": "set", "level": orig_vol}, ext.ctx)
        if orig_muted:
            fn({"command": "mute"}, ext.ctx)
        else:
            fn({"command": "unmute"}, ext.ctx)
        after = fn({"command": "get"}, ext.ctx)
        print("  restored ->", after)

    print("\nOK: volume contract verified")


if __name__ == "__main__":
    main()
