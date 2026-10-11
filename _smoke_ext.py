#!/usr/bin/env python3
"""Smoke test for the Spark extension framework (no live pipeline needed).

Run: ./.venv/bin/python _smoke_ext.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext import Manager

spoken = []
events = []


def host_emit(kind, detail):
    events.append((kind, detail))


def fake_speak(text):
    spoken.append(text)


def main():
    data_root = tempfile.mkdtemp(prefix="sparkext_test_")
    mgr = Manager(
        root=os.path.join(ROOT, "extensions"),
        data_root=data_root,
        host={"emit": host_emit, "speak": fake_speak},
        emit=lambda k, d: events.append((k, d)),
    )
    n = mgr.load_all(force=True)
    print(f"loaded {n} extension(s)")
    for s in mgr.status():
        print(f"  - {s['id']} v{s['version']} active={s['active']} "
              f"hooks={s['hooks']} scopes={s['scopes']} err={s['error']}")

    print("\n-- on_transcript dispatch --")
    cases = [
        "what time is it",
        "What's the date",
        "tell me a joke",
    ]
    for c in cases:
        out = mgr.dispatch_text("on_transcript", c)
        print(f"  in={c!r} -> out={out!r}")

    print("\n-- spoken --")
    for s in spoken:
        print("  <", s)

    print("\n-- events --")
    for k, d in events:
        print(f"  {k}: {d}")

    assert any("It is" in s for s in spoken), "time reply missing"
    assert any("Today is" in s for s in spoken), "date reply missing"
    assert mgr.dispatch_text("on_transcript", "tell me a joke") == "tell me a joke", \
        "passthrough broken"
    assert mgr.dispatch_text("on_transcript", "what time is it") == "", \
        "consumption broken"
    print("\nOK: framework contract verified")


if __name__ == "__main__":
    main()
