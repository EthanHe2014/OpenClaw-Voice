"""Smoke: fast path (opt-in per skill) — no live model, no pipeline.

Proves the gate end-to-end through the real Registry + a Manager stub:
  - opt-in skill + chat:false + success -> fast_path/fast_speech set
  - default skill (no opt-in)           -> falls back to the agent
  - chat:true or a failure              -> falls back to the agent
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sparkext.registry import Registry, Skill


class _Ext:
    def __init__(self, eid):
        self.id = eid; self.ctx = {"ext": eid}


class _Mgr:
    def __init__(self, ids):
        self.extensions = {i: _Ext(i) for i in ids}


def _route(skill, chat, ok=True):
    return {"id": f"r-{skill}-{chat}-{ok}", "transcript": "x",
            "actions": [{"skill": skill, "args": {}}], "chat": chat}


def main():
    r = Registry()
    r.register(Skill(name="clock", args={},
                     fn=lambda a, ctx: {"ok": True, "speak": "It is 4:20"},
                     ext_id="clock", fast=True))
    r.register(Skill(name="plain", args={},
                     fn=lambda a, ctx: {"ok": True, "speak": "hi"},
                     ext_id="plain"))  # no opt-in
    r.register(Skill(name="boom", args={},
                     fn=lambda a, ctx: {"ok": False, "error": "skill_error"},
                     ext_id="boom", fast=True))

    ok = r.run_route(_route("clock", False), _Mgr(["clock"]))
    assert ok["fast_path"] is True and ok["fast_speech"] == "It is 4:20", ok
    assert ok["forward_to_agent"] is False, ok
    print("PASS opt-in fast skill + chat:false -> fast_speech", repr(ok["fast_speech"]))

    no = r.run_route(_route("plain", False), _Mgr(["plain"]))
    assert no["fast_path"] is False and no["fast_speech"] is None, no
    assert no["forward_to_agent"] is False, no
    print("PASS non-opted skill -> agent (forward_to_agent=False)")

    chat = r.run_route(_route("clock", True), _Mgr(["clock"]))
    assert chat["fast_path"] is False and chat["forward_to_agent"] is True, chat
    print("PASS chat:true -> agent")

    bad = r.run_route(_route("boom", False, ok=False), _Mgr(["boom"]))
    assert bad["fast_path"] is False, bad
    print("PASS failed action -> no fast path")

    print("ALL FAST-PATH SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
