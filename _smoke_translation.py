#!/usr/bin/env python3
"""Unit test for the translation tool extension (no live pipeline needed).

Offline phrasebook: checks lookups, language aliases, failures, and that the
tool returns data and never speaks / never consumes a turn.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext import Manager

events = []


def main():
    data_root = tempfile.mkdtemp(prefix="sparkext_tr_")
    mgr = Manager(
        root=os.path.join(ROOT, "extensions"),
        data_root=data_root,
        host={"emit": lambda k, d: events.append((k, d)),
              "speak": lambda t: events.append(("SPEAK", t))},
        emit=lambda k, d: events.append((k, d)),
    )
    mgr.load_all(force=True)
    ext = mgr.extensions.get("translation")
    assert ext and ext.active, f"translation not active: {ext and ext.error}"
    fn = ext.hooks["tools"](ext.ctx)[0]["fn"]
    print("tool: translate")

    r = fn({"text": "hello", "to": "spanish"}, ext.ctx)
    assert r["ok"] and r["data"]["result"] == "hola", r
    print("  hello -> spanish:", r["data"]["result"])

    r = fn({"text": "Thank you", "to": "fr"}, ext.ctx)
    assert r["ok"] and r["data"]["result"] == "merci", r
    print("  Thank you -> fr:", r["data"]["result"])

    r = fn({"text": "good morning", "to": "中文"}, ext.ctx)
    assert r["ok"] and r["data"]["result"] == "早上好", r
    print("  good morning -> zh:", r["data"]["result"])

    r = fn({"text": "i love you", "to": "ja"}, ext.ctx)
    assert r["ok"] and r["data"]["result"] == "愛してる", r
    print("  i love you -> ja:", r["data"]["result"])

    # same language short-circuits
    r = fn({"text": "hello", "to": "en"}, ext.ctx)
    assert r["ok"] and r["data"]["result"] == "hello", r
    print("  hello -> en:", r["data"]["result"])

    # unknown phrase -> ok=False, no crash
    r = fn({"text": "quantum chromodynamics", "to": "es"}, ext.ctx)
    assert not r["ok"] and "offline" in r["error"], r
    print("  unknown phrase ->", r["error"])

    # unknown target language
    r = fn({"text": "hello", "to": "klingon"}, ext.ctx)
    assert not r["ok"] and "language" in r["error"], r
    print("  unknown lang ->", r["error"])

    # non-english source is refused
    r = fn({"text": "hola", "to": "fr", "from": "es"}, ext.ctx)
    assert not r["ok"], r
    print("  es->fr refused ->", r["error"])

    # empty text
    r = fn({"text": "", "to": "es"}, ext.ctx)
    assert not r["ok"], r
    print("  empty ->", r["error"])

    # contract: never spoke
    assert not any(k == "SPEAK" for k, _ in events), "translation spoke!"
    print("\nOK: translation contract verified")


if __name__ == "__main__":
    main()
