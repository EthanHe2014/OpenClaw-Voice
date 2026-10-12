#!/usr/bin/env python3
"""Unit test for the spelling tool extension (no live pipeline needed)."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext import Manager

events = []


def main():
    data_root = tempfile.mkdtemp(prefix="sparkext_spell_")
    mgr = Manager(
        root=os.path.join(ROOT, "extensions"),
        data_root=data_root,
        host={"emit": lambda k, d: events.append((k, d))},
        emit=lambda k, d: events.append((k, d)),
    )
    mgr.load_all(force=True)
    ext = mgr.extensions.get("spelling")
    assert ext and ext.active, f"spelling not active: {ext and ext.error}"
    fn = ext.hooks["tools"](ext.ctx)[0]["fn"]
    print("tool: spell")

    r = fn({"command": "spell", "word": "spark"}, ext.ctx)
    assert r["data"]["spelled"] == "s - p - a - r - k", r
    assert r["data"]["length"] == 5, r
    print("  spell spark ->", r["data"]["spelled"])

    r = fn({"command": "spell", "word": "well-known"}, ext.ctx)
    assert "(hyphen)" in r["data"]["letters"], r
    print("  spell hyphen ->", r["data"]["spelled"])

    r = fn({"command": "define", "word": "serendipity"}, ext.ctx)
    assert r.get("ok"), r
    assert r["data"]["found"] is True, r
    print("  define serendipity ->", r["data"]["definition"][:80], "...")

    # a nonsense word: still ok, but found=False with a note (no crash)
    r = fn({"command": "define", "word": "zzzqqqxyzzy"}, ext.ctx)
    assert r.get("ok"), r
    assert r["data"]["found"] is False, r
    print("  define nonsense -> not found:", r["data"]["note"])

    r = fn({"command": "spell", "word": ""}, ext.ctx)
    assert not r.get("ok"), r
    print("  empty word rejected ->", r["error"])

    r = fn({"command": "banana", "word": "x"}, ext.ctx)
    assert not r.get("ok"), r
    print("  bad command rejected ->", r["error"])

    print("\nOK: spelling tool verified")


if __name__ == "__main__":
    main()
