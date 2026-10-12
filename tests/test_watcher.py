"""Unit tests for the watcher router (fake Ollama, no live model).

The watcher must ALWAYS return a usable ROUTE, and must degrade to a plain
passthrough on any problem. These tests pin that invariant.
"""
import json

import pytest

from sparkext import watcher as watcher_mod
from sparkext.watcher import Watcher, ROUTE_SCHEMA


class _Resp:
    """Context-manager stand-in for urllib's response."""
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(chat_payload, tags_ok=True):
    def _open(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/api/tags"):
            if not tags_ok:
                raise OSError("connection refused")
            return _Resp({"models": []})
        return _Resp({"message": {"content": json.dumps(chat_payload)}})
    return _open


SKILLS = [
    {"name": "calculator", "description": "math", "args": {"expr": {"type": "string"}}},
    {"name": "music", "description": "music", "args": {}},
]


def test_passthrough_on_empty():
    r = Watcher().route("", SKILLS)
    assert r["intent"] == "chat" and r["actions"] == []
    assert "empty" in r["notes"]


def test_passthrough_on_no_skills():
    r = Watcher().route("hello", [])
    assert r["intent"] == "chat" and "no skills" in r["notes"]


def test_passthrough_when_model_offline(monkeypatch):
    monkeypatch.setattr(watcher_mod.urllib.request, "urlopen",
                        _fake_urlopen({}, tags_ok=False))
    r = Watcher().route("what's 2+2", SKILLS)
    assert r["intent"] == "chat" and "model offline" in r["notes"]


def test_routes_a_skill(monkeypatch):
    payload = {"intent": "skill", "confidence": 1.0, "chat": True,
               "actions": [{"skill": "calculator", "args": {"expr": "47*89"},
                            "on_error": "skip"}]}
    monkeypatch.setattr(watcher_mod.urllib.request, "urlopen",
                        _fake_urlopen(payload))
    r = Watcher().route("what's 47 times 89", SKILLS)
    assert r["intent"] == "skill"
    assert r["actions"][0]["skill"] == "calculator"
    assert r["actions"][0]["args"]["expr"] == "47*89"


def test_unknown_skill_filtered_out(monkeypatch):
    payload = {"intent": "skill", "confidence": 1.0, "chat": True,
               "actions": [{"skill": "ghost", "args": {}}]}
    monkeypatch.setattr(watcher_mod.urllib.request, "urlopen",
                        _fake_urlopen(payload))
    r = Watcher().route("do a thing", SKILLS)
    # no valid actions left -> degrade to chat
    assert r["intent"] == "chat" and r["actions"] == []


def test_low_confidence_degrades_to_passthrough(monkeypatch):
    payload = {"intent": "skill", "confidence": 0.2, "chat": True,
               "actions": [{"skill": "calculator", "args": {"expr": "1"}}]}
    monkeypatch.setattr(watcher_mod.urllib.request, "urlopen",
                        _fake_urlopen(payload))
    r = Watcher(min_confidence=0.6).route("maybe math?", SKILLS)
    assert r["intent"] == "chat" and "low confidence" in r["notes"]


def test_bad_json_degrades(monkeypatch):
    class BadResp(_Resp):
        def read(self):
            return b"not json at all"

    def _open(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/api/tags"):
            return _Resp({"models": []})
        return BadResp({})

    monkeypatch.setattr(watcher_mod.urllib.request, "urlopen", _open)
    r = Watcher().route("hi", SKILLS)
    assert r["intent"] == "chat" and "router error" in r["notes"]


def test_history_is_sent_as_messages(monkeypatch):
    seen = {}

    def _open(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/api/tags"):
            return _Resp({"models": []})
        seen["body"] = json.loads(req.data.decode())
        return _Resp({"message": {"content": json.dumps({
            "intent": "chat", "confidence": 0.9, "actions": [], "chat": True})}})

    monkeypatch.setattr(watcher_mod.urllib.request, "urlopen", _open)
    Watcher(history=4).route("turn it down", SKILLS,
                             history=["play music", "louder"])
    roles = [m["role"] for m in seen["body"]["messages"]]
    assert roles.count("user") == 3  # 2 history turns + current, plus a system msg
    assert roles[0] == "system"
    assert "turn it down" in seen["body"]["messages"][-1]["content"]


def test_route_schema_shape():
    assert "actions" in ROUTE_SCHEMA["required"]
    assert ROUTE_SCHEMA["properties"]["intent"]["enum"] == [
        "skill", "chat", "mixed", "clarify", "noop"]


# --- context window: TurnBuffer ---------------------------------------------

from sparkext.watcher import TurnBuffer  # noqa: E402


def test_turnbuffer_keeps_only_the_last_n():
    tb = TurnBuffer(3)
    for t in ("a", "b", "c", "d"):
        tb.add(t)
    assert tb.recent() == ["b", "c", "d"]      # oldest evicted
    assert len(tb) == 3


def test_turnbuffer_recent_n_and_clear():
    tb = TurnBuffer(6)
    for t in ("one", "two", "three"):
        tb.add(t)
    assert tb.recent(2) == ["two", "three"]
    assert tb.recent(0) == []
    tb.clear()
    assert tb.recent() == []


def test_turnbuffer_dedups_consecutive_and_ignores_blank():
    tb = TurnBuffer(6)
    tb.add("hello")
    tb.add("hello")          # retried utterance
    tb.add("  ")
    tb.add("")
    assert tb.recent() == ["hello"]


def test_turnbuffer_size_zero_is_inert():
    tb = TurnBuffer(0)
    tb.add("anything")
    assert tb.recent() == [] and len(tb) == 0


def test_bridge_feeds_prior_turns_as_history(monkeypatch):
    # A fake watcher records the history it is handed.
    class _W:
        def __init__(self):
            self.hist = []

        def route(self, transcript, skills, history=None):
            self.hist.append((transcript, list(history or [])))
            return {"intent": "chat", "actions": [], "transcript": transcript}

    from sparkext.bridge import Bridge

    class _Reg:
        def catalog(self):
            return []

        def run_route(self, r, m):
            return {"handled": []}

    class _Mgr:
        extensions = {}

    w = _W()
    b = Bridge(_Mgr(), _Reg(), w, history_size=4)
    b.process("play some music")
    b.process("turn it down")
    # first turn: no history; second turn: the first utterance is history
    assert w.hist[0][1] == []
    assert w.hist[1][1] == ["play some music"]


# --- model fallback ---------------------------------------------------------

def test_fallback_uses_backup_when_primary_errors(monkeypatch):
    seen = []

    def _open(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/api/tags"):
            return _Resp({"models": []})
        body = json.loads(req.data.decode())
        seen.append(body["model"])
        if body["model"] == "primary":
            raise OSError("primary down")
        return _Resp({"message": {"content": json.dumps({
            "intent": "chat", "confidence": 0.9, "actions": [], "chat": True})}})

    monkeypatch.setattr(watcher_mod.urllib.request, "urlopen", _open)
    r = Watcher(model="primary", backups=["backup"]).route("hi", SKILLS)
    assert seen == ["primary", "backup"]        # tried primary, then backup
    assert r["intent"] == "chat"


def test_fallback_passthrough_when_all_models_error(monkeypatch):
    seen = []

    def _open(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/api/tags"):
            return _Resp({"models": []})
        seen.append(json.loads(req.data.decode())["model"])
        raise OSError("nope")

    monkeypatch.setattr(watcher_mod.urllib.request, "urlopen", _open)
    r = Watcher(model="primary", backups=["backup"]).route("hi", SKILLS)
    assert seen == ["primary", "backup"]
    assert r["intent"] == "chat"
    assert "all models" in r["notes"]


def test_no_backup_keeps_old_error_note(monkeypatch):
    def _open(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/api/tags"):
            return _Resp({"models": []})
        raise OSError("down")

    monkeypatch.setattr(watcher_mod.urllib.request, "urlopen", _open)
    r = Watcher(model="primary").route("hi", SKILLS)
    assert "router error" in r["notes"]
    assert "all models" not in r["notes"]      # single model -> old phrasing


def test_backup_matching_primary_is_dropped():
    assert Watcher(model="m", backups=["m", "other"]).backups == ["other"]
