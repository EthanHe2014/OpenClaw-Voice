"""Unit tests for the bridge: transcript -> route -> result -> agent handoff.

Uses fake watcher/registry objects so no model or pipeline is touched.
"""
import json
import os

from sparkext.bridge import Bridge, format_for_agent


class _FakeWatcher:
    def __init__(self, route):
        self._route = route
        self.calls = []

    def route(self, transcript, skills, history=None):
        self.calls.append((transcript, history))
        return dict(self._route)


class _FakeRegistry:
    def __init__(self, result):
        self._result = result
        self.ran = False

    def catalog(self):
        return []

    def run_route(self, route, manager):
        self.ran = True
        return self._result


class _Mgr:
    extensions = {}


def test_chat_passthrough_skips_registry():
    route = {"intent": "chat", "actions": [], "transcript": "tell me a joke"}
    reg = _FakeRegistry({"handled": []})
    b = Bridge(_Mgr(), reg, _FakeWatcher(route))
    text, result, r = b.process("tell me a joke")
    assert text == "tell me a joke"
    assert reg.ran is False


def test_skill_route_runs_and_formats():
    route = {"intent": "skill", "actions": [{"skill": "calculator"}],
             "transcript": "what's 47 times 89", "chat": True}
    result = {"handled": ["calculator"],
              "results": [{"skill": "calculator", "ok": True, "speak": "4183",
                           "data": {"value": 4183}}],
              "failures": []}
    reg = _FakeRegistry(result)
    b = Bridge(_Mgr(), reg, _FakeWatcher(route))
    text, res, r = b.process("what's 47 times 89")
    assert reg.ran is True
    assert "4183" in text
    assert "Do NOT call this tool again" in text


def test_route_is_logged(tmp_path):
    log = tmp_path / "routes.jsonl"
    route = {"intent": "skill", "actions": [{"skill": "calculator"}],
             "transcript": "1+1", "confidence": 1.0, "notes": ""}
    result = {"handled": ["calculator"],
              "results": [{"skill": "calculator", "ok": True, "speak": "2"}],
              "failures": []}
    b = Bridge(_Mgr(), _FakeRegistry(result), _FakeWatcher(route),
               log_path=str(log))
    b.process("1+1")
    line = json.loads(log.read_text().splitlines()[0])
    assert line["handled"] == ["calculator"]
    assert line["actions"] == ["calculator"]


def test_format_for_agent_plain_when_nothing_happened():
    assert format_for_agent("hi there", {"handled": [], "results": [],
                                         "failures": []}) == "hi there"


def test_format_for_agent_lists_failures():
    result = {"handled": [], "results": [],
              "failures": [{"skill": "weather", "error": "skill_error"}]}
    out = format_for_agent("what's the weather", result)
    assert "FAILED" in out and "weather" in out


def test_format_for_agent_confirmation_prompt():
    result = {"handled": [], "results": [],
              "failures": [{"skill": "danger", "error": "needs_confirmation"}],
              "needs_confirmation": True}
    out = format_for_agent("delete it", result)
    assert "confirmation" in out
    # needs_confirmation itself is not reported as a hard failure
    assert "FAILED" not in out


def test_format_for_agent_dict_output_is_json():
    result = {"handled": ["stats"],
              "results": [{"skill": "stats", "ok": True, "speak": None,
                           "data": {"battery": 80}}],
              "failures": []}
    out = format_for_agent("battery", result)
    assert '"battery": 80' in out


# --- confirm gate -----------------------------------------------------------

from sparkext.bridge import _confirmation_verdict  # noqa: E402


def test_confirmation_verdict_yes_no_none():
    for y in ("yes", "Yeah", "yep!", "sure", "do it", "go ahead", "ok"):
        assert _confirmation_verdict(y) == "yes", y
    for n in ("no", "Nope", "cancel", "stop", "do not", "never mind"):
        assert _confirmation_verdict(n) == "no", n
    # a normal sentence is not a verdict
    assert _confirmation_verdict("what's the weather tomorrow") is None
    assert _confirmation_verdict("") is None


class _FakeWatcherSeq:
    """Returns queued routes in order, so one test can drive two utterances."""
    def __init__(self, routes):
        self._routes = list(routes)
        self.calls = []

    def route(self, transcript, skills, history=None):
        self.calls.append(transcript)
        return dict(self._routes.pop(0) if self._routes else
                    {"intent": "chat", "actions": [], "transcript": transcript})


class _ConfirmRegistry:
    """Emulates run_route's confirm gate: never runs a confirm:true action."""
    def __init__(self):
        self.ran = []

    def catalog(self):
        return []

    def run_route(self, route, manager):
        results = []
        handled = []
        needs = False
        for a in route.get("actions", []):
            if a.get("confirm"):
                needs = True
                results.append({"skill": a["skill"], "ok": False,
                                "error": "needs_confirmation"})
                continue
            self.ran.append(a["skill"])
            handled.append(a["skill"])
            results.append({"skill": a["skill"], "ok": True, "speak": "done"})
        return {"handled": handled, "results": results, "failures": [],
                "needs_confirmation": needs, "forward_to_agent": True}


def test_confirm_blocks_then_yes_runs_the_action():
    route = {"id": "r-c1", "intent": "skill",
             "actions": [{"skill": "danger", "args": {}, "confirm": True}],
             "transcript": "delete everything", "chat": True}
    reg = _ConfirmRegistry()
    w = _FakeWatcherSeq([route])
    b = Bridge(_Mgr(), reg, w)

    text, res, _ = b.process("delete everything")
    assert reg.ran == []                       # nothing ran on the first turn
    assert res["needs_confirmation"] is True
    assert "confirmation" in text.lower()
    assert b._pending is not None

    text2, res2, _ = b.process("yes")
    assert reg.ran == ["danger"]               # ran only after the yes
    assert res2["handled"] == ["danger"]
    assert b._pending is None


def test_confirm_no_drops_the_action():
    route = {"id": "r-c2", "intent": "skill",
             "actions": [{"skill": "danger", "args": {}, "confirm": True}],
             "transcript": "delete everything", "chat": True}
    reg = _ConfirmRegistry()
    b = Bridge(_Mgr(), reg, _FakeWatcherSeq([route]))
    b.process("delete everything")
    assert b._pending is not None
    text, res, _ = b.process("no")
    assert reg.ran == []                       # declined: never runs
    assert b._pending is None
    assert "DECLINED" in text


def test_confirm_ignores_an_unrelated_followup():
    route = {"id": "r-c3", "intent": "skill",
             "actions": [{"skill": "danger", "args": {}, "confirm": True}],
             "transcript": "delete everything", "chat": True}
    reg = _ConfirmRegistry()
    follow = {"intent": "chat", "actions": [], "transcript": "never mind that"}
    b = Bridge(_Mgr(), reg, _FakeWatcherSeq([route, follow]))
    b.process("delete everything")
    assert b._pending is not None
    b.process("what's the weather in paris")   # a question, not a yes/no
    assert reg.ran == []                       # the pending action is dropped
    assert b._pending is None


def test_confirmed_run_uses_fresh_route_id():
    route = {"id": "r-c4", "intent": "skill",
             "actions": [{"skill": "danger", "args": {}, "confirm": True},
                         {"skill": "safe", "args": {}}],
             "transcript": "do the thing", "chat": True}
    reg = _ConfirmRegistry()
    b = Bridge(_Mgr(), reg, _FakeWatcherSeq([route]))
    b.process("do the thing")
    seen_ids = []
    orig = reg.run_route

    def spy(r, m):
        seen_ids.append(r["id"])
        return orig(r, m)

    reg.run_route = spy
    b.process("yes")
    assert seen_ids == ["r-c4-confirmed"]
    assert reg.ran == ["safe", "danger"]      # only the blocked action, re-run
