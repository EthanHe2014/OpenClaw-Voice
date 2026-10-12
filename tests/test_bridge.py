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
