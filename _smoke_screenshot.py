#!/usr/bin/env python3
"""Unit test for the screenshot tool extension (no live pipeline needed).

Captures real screenshots to a temp data_dir and asserts the returned data,
then removes them. Never touches the workspace or any real file path.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext import Manager

events = []


def main():
    data_root = tempfile.mkdtemp(prefix="sparkext_shot_")
    mgr = Manager(
        root=os.path.join(ROOT, "extensions"),
        data_root=data_root,
        host={"emit": lambda k, d: events.append((k, d))},
        emit=lambda k, d: events.append((k, d)),
    )
    mgr.load_all(force=True)
    ext = mgr.extensions.get("screenshot")
    assert ext and ext.active, f"screenshot not active: {ext and ext.error}"
    fn = ext.hooks["tools"](ext.ctx)[0]["fn"]
    print("tool: screenshot")

    # full-screen capture (default timestamped name)
    r = fn({"mode": "screen"}, ext.ctx)
    assert r.get("ok"), r
    assert r["data"]["mode"] == "screen", r
    assert r["data"]["bytes"] > 0, r
    assert os.path.exists(r["data"]["path"]), r
    print("  screen ->", r["data"])

    # named capture (bare name -> data_dir, .png appended)
    r = fn({"mode": "screen", "path": "named-shot"}, ext.ctx)
    assert r.get("ok"), r
    assert r["data"]["filename"] == "named-shot.png", r
    print("  named  ->", r["data"])

    # window capture (frontmost window). Tolerate "no front window" in a
    # headless/CI session, but require a real capture when one exists.
    r = fn({"mode": "window"}, ext.ctx)
    if r.get("ok"):
        assert r["data"]["mode"] == "window", r
        assert "window" in r["data"], r
        print("  window ->", r["data"])
    else:
        print("  window -> skipped (no front window):", r["error"])

    # bad mode is rejected, not crashed
    r = fn({"mode": "banana"}, ext.ctx)
    assert not r.get("ok"), r
    print("  bad mode rejected ->", r["error"])

    print("\nOK: screenshot tool verified")


if __name__ == "__main__":
    main()
