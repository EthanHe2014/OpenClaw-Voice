#!/usr/bin/env python3
"""Unit test for the appcontrol tool extension (no live pipeline needed)."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext import Manager

events = []


def main():
    data_root = tempfile.mkdtemp(prefix="sparkext_app_")
    mgr = Manager(
        root=os.path.join(ROOT, "extensions"),
        data_root=data_root,
        host={"emit": lambda k, d: events.append((k, d))},
        emit=lambda k, d: events.append((k, d)),
    )
    mgr.load_all(force=True)
    ext = mgr.extensions.get("appcontrol")
    assert ext and ext.active, f"appcontrol not active: {ext and ext.error}"
    decls = ext.hooks["tools"](ext.ctx)
    fn = decls[0]["fn"]
    print("tool:", decls[0]["name"], "-", decls[0]["description"][:70], "....")

    # Finder is always running -> safe, no side effect
    r = fn({"command": "running", "name": "Finder"}, ext.ctx)
    assert r.get("ok") and r["data"]["running"] is True, r
    print("  running Finder ->", r)

    # bogus app running check -> ok:False, graceful
    r = fn({"command": "running", "name": "NoSuchAppZzz123"}, ext.ctx)
    print("  running bogus ->", r)

    # missing name -> error
    r = fn({"command": "open", "name": ""}, ext.ctx)
    assert not r.get("ok"), r
    print("  open '' ->", r)

    # unknown command -> error, no execution
    r = fn({"command": "frobnicate", "name": "Finder"}, ext.ctx)
    assert not r.get("ok") and "unknown" in r["error"], r
    print("  bad cmd ->", r)

    print("\nOK: appcontrol contract verified")


if __name__ == "__main__":
    main()
