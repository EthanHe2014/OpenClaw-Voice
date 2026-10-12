"""Unit tests for the skill registry: typed arg validation + route execution.

No live model, no pipeline. Run: ./.venv/bin/python -m pytest tests -q
"""
import pytest

from sparkext.registry import Registry, Skill, _validate_args, _coerce_arg


# --- arg coercion -----------------------------------------------------------

SPEC = {
    "command": {"type": "enum", "required": True, "values": ["a", "b"]},
    "level": {"type": "number", "required": False},
    "word": {"type": "string", "required": False},
    "when": {"type": "date", "required": False},
    "dur": {"type": "duration", "required": False},
    "flag": {"type": "bool", "required": False},
}


def test_numeric_string_coerced_to_number():
    ok, err, out = _validate_args(SPEC, {"command": "a", "level": "42"})
    assert ok and out["level"] == 42.0


def test_number_kept_as_int():
    ok, err, out = _validate_args(SPEC, {"command": "a", "level": 3})
    assert ok and out["level"] == 3


def test_bad_enum_rejected_with_arg_name():
    ok, err, out = _validate_args(SPEC, {"command": "z"})
    assert not ok and err["error"] == "invalid_args" and err["arg"] == "command"


def test_non_numeric_rejected():
    ok, err, out = _validate_args(SPEC, {"command": "a", "level": "lots"})
    assert not ok and err["error"] == "invalid_args"


def test_bool_is_not_a_number():
    ok, err, out = _validate_args(SPEC, {"command": "a", "level": True})
    assert not ok and err["error"] == "invalid_args"


def test_date_accepts_phrase_rejects_int():
    ok, _, out = _validate_args(SPEC, {"command": "a", "when": "3pm"})
    assert ok and out["when"] == "3pm"
    ok, err, _ = _validate_args(SPEC, {"command": "a", "when": 5})
    assert not ok


def test_duration_accepts_phrase_or_seconds():
    ok, _, out = _validate_args(SPEC, {"command": "a", "dur": "5 minutes"})
    assert ok and out["dur"] == "5 minutes"
    ok, _, out = _validate_args(SPEC, {"command": "a", "dur": 90})
    assert ok and out["dur"] == 90


def test_bool_coerce():
    assert _coerce_arg({"type": "bool"}, "f", "yes") == (True, True, None)
    assert _coerce_arg({"type": "bool"}, "f", "false") == (True, False, None)
    assert _coerce_arg({"type": "bool"}, "f", 1) == (True, True, None)
    ok, _, _ = _coerce_arg({"type": "bool"}, "f", "maybe")
    assert not ok


def test_string_stringifies_number():
    ok, _, out = _validate_args(SPEC, {"command": "a", "word": 7})
    assert ok and out["word"] == "7"


def test_missing_required_reported_not_guessed():
    ok, err, out = _validate_args(SPEC, {"level": 1})
    assert not ok and err["error"] == "missing_args" and err["missing"] == ["command"]


# --- run_one dispatch -------------------------------------------------------

def _reg(fn):
    r = Registry()
    r.register(Skill(
        name="probe",
        args={"command": {"type": "enum", "required": True, "values": ["a"]},
              "level": {"type": "number", "required": False}},
        fn=fn, ext_id="probe"))
    return r


def test_run_one_passes_coerced_args():
    seen = {}

    def fn(args, ctx):
        seen.update(args)
        return {"ok": True, "data": {"echo": args.get("level")}}

    res = _reg(fn).run_one({"skill": "probe", "args": {"command": "a", "level": "9"}}, {})
    assert res["ok"] and seen["level"] == 9.0


def test_run_one_reports_bad_arg():
    res = _reg(lambda a, ctx: {"ok": True}).run_one(
        {"skill": "probe", "args": {"command": "x"}}, {})
    assert not res["ok"] and res["arg"] == "command" and res["error"] == "invalid_args"


def test_run_one_unknown_skill():
    res = Registry().run_one({"skill": "nope", "args": {}}, {})
    assert not res["ok"] and res["error"] == "unknown_skill"


def test_run_one_contains_skill_exception():
    def boom(args, ctx):
        raise ValueError("kaboom")

    res = _reg(boom).run_one({"skill": "probe", "args": {"command": "a"}}, {})
    assert not res["ok"] and "kaboom" in res["error"]


def test_run_one_failure_emits_event():
    events = []
    r = Registry(emit=lambda k, d: events.append((k, d)))
    r.register(Skill(name="probe", args={}, fn=lambda a, ctx: {"ok": False,
                                                              "error": "nope"},
                     ext_id="probe"))
    res = r.run_one({"skill": "probe", "args": {}}, {})
    assert not res["ok"]
    assert any(k == "WATCHER" and "skill failed" in d for k, d in events)


# --- run_route: the RESULT document ----------------------------------------

class _Mgr:
    """Minimal manager stub: run_route only reads .extensions.values().ctx."""
    def __init__(self, ext_ids):
        self.extensions = {e: _Ext(e) for e in ext_ids}


class _Ext:
    def __init__(self, eid):
        self.id = eid
        self.ctx = {"ext": eid}


def test_run_route_collects_handled_and_result():
    r = Registry()
    r.register(Skill(name="calc", args={},
                     fn=lambda a, ctx: {"ok": True, "speak": "4183",
                                        "data": {"value": 4183}}, ext_id="calc"))
    route = {"id": "r-1", "transcript": "47*89", "actions":
             [{"skill": "calc", "args": {}}], "chat": True}
    res = r.run_route(route, _Mgr(["calc"]))
    assert res["handled"] == ["calc"]
    assert res["results"][0]["speak"] == "4183"
    assert res["forward_to_agent"] is True
    assert "already handled" in res["agent_note"]


def test_run_route_missing_args_action():
    r = Registry()
    r.register(Skill(name="calc", args={}, fn=lambda a, ctx: {"ok": True},
                     ext_id="calc"))
    route = {"id": "r-2", "transcript": "x", "actions":
             [{"skill": "calc", "args": {}, "missing": ["expr"]}], "chat": True}
    res = r.run_route(route, _Mgr(["calc"]))
    assert res["failures"][0]["error"] == "missing_args"
    assert res["failures"][0]["missing"] == ["expr"]


def test_run_route_confirm_gate_blocks():
    ran = []
    r = Registry()
    r.register(Skill(name="danger", args={},
                     fn=lambda a, ctx: ran.append(1) or {"ok": True},
                     ext_id="danger"))
    route = {"id": "r-3", "transcript": "delete", "actions":
             [{"skill": "danger", "args": {}, "confirm": True}], "chat": True}
    res = r.run_route(route, _Mgr(["danger"]))
    assert not ran and res["needs_confirmation"]
    assert res["results"][0]["error"] == "needs_confirmation"


def test_run_route_failure_is_visible():
    r = Registry()
    r.register(Skill(name="bad", args={}, fn=lambda a, ctx: {"ok": False,
                                                             "error": "skill_error"},
                     ext_id="bad"))
    route = {"id": "r-4", "transcript": "x", "actions":
             [{"skill": "bad", "args": {}}], "chat": True}
    res = r.run_route(route, _Mgr(["bad"]))
    assert res["handled"] == []
    assert res["failures"][0]["skill"] == "bad"


def test_run_route_critical_abort_stops_later_actions():
    r = Registry()
    r.register(Skill(name="bad", args={}, fn=lambda a, ctx: {"ok": False,
                                                             "error": "skill_error"},
                     ext_id="bad"))
    r.register(Skill(name="after", args={}, fn=lambda a, ctx: {"ok": True},
                     ext_id="after"))
    route = {"id": "r-5", "transcript": "x", "actions": [
        {"skill": "bad", "args": {}, "critical": True, "on_error": "abort"},
        {"skill": "after", "args": {}},
    ], "chat": True}
    res = r.run_route(route, _Mgr(["bad", "after"]))
    assert [r_["skill"] for r_ in res["results"]] == ["bad"]


def test_forward_to_agent_false_when_pure_skill_with_output():
    r = Registry()
    r.register(Skill(name="calc", args={},
                     fn=lambda a, ctx: {"ok": True, "speak": "2"}, ext_id="calc"))
    route = {"id": "r-6", "transcript": "1+1", "actions":
             [{"skill": "calc", "args": {}}], "chat": False}
    res = r.run_route(route, _Mgr(["calc"]))
    assert res["forward_to_agent"] is False


# --- idempotency ------------------------------------------------------------

def test_repeated_route_id_runs_once():
    calls = []
    r = Registry()
    r.register(Skill(name="calc", args={},
                     fn=lambda a, ctx: calls.append(1) or {"ok": True, "speak": "2"},
                     ext_id="calc"))
    route = {"id": "r-dup", "transcript": "1+1", "actions":
             [{"skill": "calc", "args": {}}], "chat": True}
    first = r.run_route(route, _Mgr(["calc"]))
    second = r.run_route(route, _Mgr(["calc"]))
    assert calls == [1]  # the skill body ran exactly once
    assert first["handled"] == ["calc"]
    assert second["handled"] == ["calc"] and second.get("deduped") is True


def test_distinct_route_ids_both_run():
    calls = []
    r = Registry()
    r.register(Skill(name="calc", args={},
                     fn=lambda a, ctx: calls.append(1) or {"ok": True},
                     ext_id="calc"))
    base = {"transcript": "1+1", "actions": [{"skill": "calc", "args": {}}],
            "chat": True}
    r.run_route(dict(base, id="r-a"), _Mgr(["calc"]))
    r.run_route(dict(base, id="r-b"), _Mgr(["calc"]))
    assert calls == [1, 1]


def test_missing_route_id_never_dedups():
    calls = []
    r = Registry()
    r.register(Skill(name="calc", args={},
                     fn=lambda a, ctx: calls.append(1) or {"ok": True},
                     ext_id="calc"))
    route = {"transcript": "1+1", "actions": [{"skill": "calc", "args": {}}],
             "chat": True}
    r.run_route(route, _Mgr(["calc"]))
    r.run_route(route, _Mgr(["calc"]))
    assert calls == [1, 1]


def test_dedup_can_be_disabled():
    calls = []
    r = Registry(dedup=False)
    r.register(Skill(name="calc", args={},
                     fn=lambda a, ctx: calls.append(1) or {"ok": True},
                     ext_id="calc"))
    route = {"id": "r-x", "transcript": "1+1", "actions":
             [{"skill": "calc", "args": {}}], "chat": True}
    r.run_route(route, _Mgr(["calc"]))
    r.run_route(route, _Mgr(["calc"]))
    assert calls == [1, 1]


def test_dedup_cache_is_bounded():
    r = Registry(seen_max=3)
    r.register(Skill(name="calc", args={}, fn=lambda a, ctx: {"ok": True},
                     ext_id="calc"))
    for i in range(5):
        r.run_route({"id": f"r-{i}", "transcript": "x",
                     "actions": [{"skill": "calc", "args": {}}], "chat": True},
                    _Mgr(["calc"]))
    assert len(r._seen) == 3
    # the two oldest were evicted, so they run again
    assert "r-0" not in r._seen and "r-4" in r._seen


# --- chaining (depends_on) --------------------------------------------------

def test_depends_on_feeds_prior_data_into_next_args():
    """The whole point: a later action consumes an earlier one's OUTPUT."""
    r = Registry()
    r.register(Skill(name="fetch", args={},
                     fn=lambda a, ctx: {"ok": True,
                                        "data": {"city": "Shanghai"}},
                     ext_id="fetch"))
    seen = {}

    def consume(args, ctx):
        seen.update(args)
        return {"ok": True, "speak": "18 degrees in " + args["city"]}

    r.register(Skill(name="weather", args={"city": {"type": "string",
                                                     "required": True}},
                     fn=consume, ext_id="weather"))
    route = {"id": "r-chain", "transcript": "where am I and how hot",
             "actions": [
                 {"skill": "fetch", "args": {}},
                 {"skill": "weather", "args": {}, "depends_on": 0},
             ], "chat": True}
    res = r.run_route(route, _Mgr(["fetch", "weather"]))
    assert res["handled"] == ["fetch", "weather"]
    assert seen["city"] == "Shanghai"          # output of action 0 reached action 1
    assert res["results"][1]["speak"] == "18 degrees in Shanghai"
    assert not res["failures"]


def test_depends_on_explicit_args_override_upstream():
    r = Registry()
    r.register(Skill(name="fetch", args={},
                     fn=lambda a, ctx: {"ok": True, "data": {"from": "A",
                                                              "to": "C"}},
                     ext_id="fetch"))
    seen = {}
    r.register(Skill(name="convert",
                     args={"from": {"type": "string", "required": False},
                           "to": {"type": "string", "required": False}},
                     fn=lambda a, ctx: seen.update(a) or {"ok": True},
                     ext_id="convert"))
    route = {"id": "r-over", "transcript": "x", "actions": [
        {"skill": "fetch", "args": {}},
        {"skill": "convert", "args": {"to": "F"}, "depends_on": 0},
    ], "chat": True}
    r.run_route(route, _Mgr(["fetch", "convert"]))
    assert seen["from"] == "A" and seen["to"] == "F"  # own arg wins


def test_depends_on_failed_upstream_blocks_downstream():
    ran = []
    r = Registry(emit=lambda k, d: None)
    r.register(Skill(name="fetch", args={},
                     fn=lambda a, ctx: {"ok": False, "error": "skill_error"},
                     ext_id="fetch"))
    r.register(Skill(name="weather", args={},
                     fn=lambda a, ctx: ran.append(1) or {"ok": True},
                     ext_id="weather"))
    route = {"id": "r-fail", "transcript": "x", "actions": [
        {"skill": "fetch", "args": {}},
        {"skill": "weather", "args": {}, "depends_on": 0},
    ], "chat": True}
    res = r.run_route(route, _Mgr(["fetch", "weather"]))
    assert ran == []                                   # downstream never ran
    assert res["results"][1]["error"] == "dependency_failed"
    assert [f["skill"] for f in res["failures"]] == ["fetch", "weather"]


def test_depends_on_invalid_index_reported():
    r = Registry(emit=lambda k, d: None)
    r.register(Skill(name="calc", args={}, fn=lambda a, ctx: {"ok": True},
                     ext_id="calc"))
    # forward/self reference is invalid (must point to an EARLIER action)
    route = {"id": "r-bad", "transcript": "x", "actions": [
        {"skill": "calc", "args": {}, "depends_on": 1},
        {"skill": "calc", "args": {}},
    ], "chat": True}
    res = r.run_route(route, _Mgr(["calc"]))
    assert res["results"][0]["error"] == "dependency_failed"


def test_depends_on_none_is_normal():
    r = Registry()
    r.register(Skill(name="calc", args={}, fn=lambda a, ctx: {"ok": True},
                     ext_id="calc"))
    route = {"id": "r-plain", "transcript": "x", "actions": [
        {"skill": "calc", "args": {}, "depends_on": None},
        {"skill": "calc", "args": {}},
    ], "chat": True}
    res = r.run_route(route, _Mgr(["calc"]))
    assert res["handled"] == ["calc", "calc"] and not res["failures"]
