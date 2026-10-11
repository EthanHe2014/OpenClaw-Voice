"""Watcher — the LLM skill-router.

Sees the STT transcript (+ a little history), decides what should happen, and
emits a ROUTE (see WATCHER.md). It NEVER runs a skill itself.

Brain: a local model via Ollama, called with a JSON-schema constraint so the
output physically cannot be malformed. On ANY problem (model down, timeout,
bad JSON, low confidence) it returns a plain PASSTHROUGH route — the system then
behaves exactly like before. This is the invariant that makes it safe.
"""
import json
import time
import uuid
import urllib.request


# --- the JSON schema the model must emit (structured outputs) ----------------
ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["skill", "chat", "mixed", "clarify", "noop"]},
        "confidence": {"type": "number"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "args": {"type": "object"},
                    "missing": {"type": "array", "items": {"type": "string"}},
                    "depends_on": {"type": ["integer", "null"]},
                    "confirm": {"type": "boolean"},
                    "on_error": {"type": "string", "enum": ["skip", "ask", "abort"]}
                },
                "required": ["skill", "args"]
            }
        },
        "chat": {"type": "boolean"},
        "clarify": {"type": ["string", "null"]},
        "notes": {"type": "string"}
    },
    "required": ["intent", "confidence", "actions", "chat"]
}


def _passthrough(transcript, why):
    return {
        "v": 1, "id": "r-" + uuid.uuid4().hex[:6],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tz": time.strftime("%Z"), "transcript": transcript,
        "intent": "chat", "confidence": 0.0, "actions": [],
        "chat": True, "clarify": None, "notes": f"passthrough ({why})",
    }


class Watcher:
    def __init__(self, model="qwen2.5:7b", url="http://127.0.0.1:11434",
                 min_confidence=0.6, timeout=8.0, history=6, verbose=False):
        self.model = model
        self.url = url.rstrip("/")
        self.min_confidence = min_confidence
        self.timeout = timeout
        self.history = history
        self.verbose = verbose

    def available(self):
        """Is the local model server up?"""
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def _catalog(self, skills):
        """Render the skill catalog the model chooses from. `skills` is a list of
        dicts (from the registry) with name/description/args/examples."""
        lines = []
        for s in skills:
            args = ", ".join(
                f"{k}:{v.get('type','string')}" + ("" if v.get("required") else "?")
                for k, v in (s.get("args") or {}).items()) or "(no args)"
            lines.append(f"- {s['name']}: {s.get('description','')}  args[{args}]")
        return "\n".join(lines) if lines else "(no skills available)"

    def _system_prompt(self, skills, now, tz):
        return (
            "You are the ROUTER for a voice assistant. You do NOT answer the user "
            "and you do NOT run anything. You only decide what should happen and "
            "emit JSON.\n\n"
            f"Current time: {now}  (timezone {tz})\n\n"
            "Available skills:\n" + self._catalog(skills) + "\n\n"
            "Rules:\n"
            "- If the message maps to one or more skills, list them in `actions` "
            "with their args. Use intent 'skill' (only skills) or 'mixed' (skills "
            "plus something to say).\n"
            "- If it is only conversation, use intent 'chat' with no actions.\n"
            "- Convert relative times to the literal phrase the skill expects; do "
            "NOT compute absolute datetimes yourself.\n"
            "- If a required arg is unknown, put its name in `missing` and do NOT guess.\n"
            "- If the whole request is ambiguous, use intent 'clarify' and put the "
            "question in `clarify`.\n"
            "- Set confidence 0..1. If unsure, prefer 'chat' with low confidence.\n"
            "- Set `chat` true if a spoken reply is still wanted (almost always true)."
        )

    def route(self, transcript, skills, history=None):
        """Return a ROUTE dict. Always returns something usable."""
        transcript = (transcript or "").strip()
        if not transcript:
            return _passthrough(transcript, "empty")
        if not skills:
            return _passthrough(transcript, "no skills")
        if not self.available():
            return _passthrough(transcript, "model offline")

        msgs = [{"role": "system", "content": self._system_prompt(
            skills, time.strftime("%Y-%m-%d %H:%M"), time.strftime("%Z"))}]
        for turn in (history or [])[-self.history:]:
            msgs.append({"role": "user", "content": str(turn)})
        msgs.append({"role": "user", "content": transcript})

        body = {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "format": ROUTE_SCHEMA,
            "options": {"temperature": 0},
        }
        try:
            req = urllib.request.Request(
                self.url + "/api/chat", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = json.loads(r.read().decode())
            content = (resp.get("message") or {}).get("content") or ""
            data = json.loads(content)
        except Exception as e:
            return _passthrough(transcript, f"router error {type(e).__name__}")

        # Validate + assemble the envelope.
        conf = float(data.get("confidence", 0) or 0)
        if data.get("intent") in ("skill", "mixed") and conf < self.min_confidence:
            return _passthrough(transcript, f"low confidence {conf:.2f}")

        known = {s["name"] for s in skills}
        actions = []
        for a in (data.get("actions") or []):
            if a.get("skill") not in known:
                continue
            actions.append({
                "skill": a["skill"],
                "args": a.get("args") or {},
                "missing": a.get("missing") or [],
                "depends_on": a.get("depends_on"),
                "confirm": bool(a.get("confirm", False)),
                "on_error": a.get("on_error", "skip"),
                "timeout_ms": a.get("timeout_ms", 3000),
            })
        intent = data.get("intent", "chat")
        if intent in ("skill", "mixed") and not actions:
            intent = "chat"

        return {
            "v": 1, "id": "r-" + uuid.uuid4().hex[:6],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tz": time.strftime("%Z"), "transcript": transcript,
            "intent": intent, "confidence": round(conf, 3),
            "actions": actions, "chat": bool(data.get("chat", True)),
            "clarify": data.get("clarify"),
            "notes": data.get("notes", ""),
        }
