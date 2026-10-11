#!/usr/bin/env python3
"""Smoke test for the watcher stack: watcher -> runner -> agent handoff.

Uses a STUBBED router so it needs no model, proving the plumbing. When the real
model is up, run `--live` to test the actual LLM routing.

Run: ./.venv/bin/python _smoke_watcher.py [--live]
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext import Manager, Registry, Bridge
from sparkext.bridge import format_for_agent

events = []


class StubWatcher:
    """Returns a canned ROUTE so we can test the runner + handoff offline."""
    def __init__(self, route):
        self._route = route
    def route(self, transcript, skills, history=None):
        r = dict(self._route)
        r["transcript"] = transcript
        return r


def main():
    live = "--live" in sys.argv
    data_root = tempfile.mkdtemp(prefix="sparkext_wtest_")
    mgr = Manager(root=os.path.join(ROOT, "extensions"), data_root=data_root,
                  host={"emit": lambda k, d: events.append((k, d)),
                        "speak": lambda t: events.append(("SPEAK", t))},
                  emit=lambda k, d: events.append((k, d)))
    mgr.load_all(force=True)
    reg = Registry().collect_from(mgr)
    print("skills:", [s["name"] for s in reg.catalog()])

    if live:
        from sparkext import Watcher
        w = Watcher()
        print("model available:", w.available())
        for text in ["what's 47 times 89",
                     "remind me to stretch in 20 minutes",
                     "tell me a joke"]:
            route = w.route(text, reg.catalog())
            print(f"\nin: {text!r}\n  intent={route['intent']} conf={route['confidence']} "
                  f"actions={[a['skill'] for a in route['actions']]} notes={route['notes']!r}")
            if route["actions"]:
                res = reg.run_route(route, mgr)
                print("  result:", {k: res[k] for k in ("handled", "forward_to_agent")})
                print("  agent_text:\n" + format_for_agent(text, res))
        return

    # offline test: simulate a mixed route
    route = {
        "v": 1, "id": "r-test01", "ts": "2026-10-11T16:00:00+08:00", "tz": "CST",
        "intent": "mixed", "confidence": 0.9, "chat": True, "clarify": None,
        "notes": "calc + chat",
        "actions": [{"skill": "calculator", "args": {"expr": "47*89"},
                     "on_error": "skip", "confirm": False}],
        "transcript": "what's 47 times 89",
    }
    res = reg.run_route(route, mgr)
    print("\nresult:", {k: res[k] for k in ("handled", "forward_to_agent")})
    print("results:", res["results"])
    print("\nagent_text (what OpenClaw receives):\n" + "-" * 50)
    print(format_for_agent(route["transcript"], res))
    print("-" * 50)

    assert res["handled"] == ["calculator"], res
    assert any(r.get("speak") == "4183" for r in res["results"]), res
    txt = format_for_agent(route["transcript"], res)
    assert "already handled" in txt and "calculator -> 4183" in txt
    assert "Do NOT redo" in txt

    # unknown skill should be reported, not crash
    bad = dict(route); bad["actions"] = [{"skill": "nope", "args": {}}]
    rb = reg.run_route(bad, mgr)
    assert rb["results"][0]["error"] == "unknown_skill"
    print("\nunknown-skill handled:", rb["results"][0])

    print("\nOK: watcher stack verified")


if __name__ == "__main__":
    main()
