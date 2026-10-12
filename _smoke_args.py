#!/usr/bin/env python3
"""Unit test for typed arg-schema enforcement in the registry (no pipeline)."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sparkext.registry import _validate_args, Registry, Skill


def main():
    spec = {
        "command": {"type": "enum", "required": True, "values": ["a", "b"]},
        "level": {"type": "number", "required": False},
        "word": {"type": "string", "required": False},
        "when": {"type": "date", "required": False},
        "dur": {"type": "duration", "required": False},
        "flag": {"type": "bool", "required": False},
    }

    ok, err, out = _validate_args(spec, {"command": "a", "level": "42"})
    assert ok and out["level"] == 42.0, (ok, err, out)
    print("  numeric string coerced ->", out)

    ok, err, out = _validate_args(spec, {"command": "a", "level": 3})
    assert ok and out["level"] == 3, (ok, err, out)
    print("  int kept ->", out)

    ok, err, out = _validate_args(spec, {"command": "z"})
    assert not ok and err["error"] == "invalid_args" and err["arg"] == "command"
    print("  bad enum rejected ->", err["detail"])

    ok, err, out = _validate_args(spec, {"command": "a", "level": "lots"})
    assert not ok and err["error"] == "invalid_args", (ok, err)
    print("  non-numeric rejected ->", err["detail"])

    ok, err, out = _validate_args(spec, {"command": "a", "when": "3pm"})
    assert ok and out["when"] == "3pm", (ok, err, out)
    ok, err, out = _validate_args(spec, {"command": "a", "when": 5})
    assert not ok, (ok, err)
    print("  date type enforced")

    ok, err, out = _validate_args(spec, {"command": "a", "dur": "5 minutes"})
    assert ok and out["dur"] == "5 minutes", (ok, err, out)
    ok, err, out = _validate_args(spec, {"command": "a", "dur": 90})
    assert ok and out["dur"] == 90, (ok, err, out)
    print("  duration accepts phrase or seconds")

    ok, err, out = _validate_args(spec, {"command": "a", "word": 7})
    assert ok and out["word"] == "7", (ok, err, out)
    print("  number-ish string arg coerced to str")

    ok, err, out = _validate_args(spec, {"command": "a", "flag": "yes"})
    assert ok and out["flag"] is True, (ok, err, out)
    ok, err, out = _validate_args(spec, {"command": "a", "flag": "false"})
    assert ok and out["flag"] is False, (ok, err, out)
    ok, err, out = _validate_args(spec, {"command": "a", "flag": "maybe"})
    assert not ok, (ok, err)
    print("  bool coerced/validated")

    ok, err, out = _validate_args(spec, {"level": 1})
    assert not ok and err["error"] == "missing_args" and err["missing"] == ["command"]
    print("  missing required reported")

    # run_one surfaces arg errors with an `arg` field
    reg = Registry()
    seen = {}

    def fake(args, ctx):
        seen.update(args)
        return {"ok": True, "data": {"echo": args.get("level")}}

    reg.register(Skill(name="probe",
                       args={"command": {"type": "enum", "required": True,
                                         "values": ["a"]},
                             "level": {"type": "number", "required": False}},
                       fn=fake, ext_id="probe"))
    res = reg.run_one({"skill": "probe", "args": {"command": "a", "level": "9"}},
                      {})
    assert res["ok"] and seen["level"] == 9.0, (res, seen)
    print("  run_one passes coerced args ->", seen)

    res = reg.run_one({"skill": "probe", "args": {"command": "x"}}, {})
    assert not res["ok"] and res["arg"] == "command", res
    print("  run_one reports bad enum ->", res["detail"])

    print("\nOK: arg schemas verified")


if __name__ == "__main__":
    main()
