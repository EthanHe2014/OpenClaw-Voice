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
from collections import deque


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


class TurnBuffer:
    """Bounded ring of the last few user utterances (oldest first).

    The watcher sees these so it can resolve pronouns/ellipsis ("turn *it*
    down"). Stdlib only, O(1) push, and it can never raise into the pipeline:
    a bad push just yields empty history, i.e. exactly the old behaviour.
    """

    def __init__(self, size=6):
        self.size = max(0, int(size))
        self._turns = deque(maxlen=self.size)

    def add(self, text):
        t = (text or "").strip()
        if not t or self.size == 0:
            return
        if self._turns and self._turns[-1] == t:
            return                      # a retried/duplicated utterance
        self._turns.append(t)

    def recent(self, n=None):
        if self.size == 0 or not self._turns:
            return []
        n = self.size if n is None else max(0, int(n))
        if n == 0:
            return []
        return list(self._turns)[-n:]

    def clear(self):
        self._turns.clear()

    def __len__(self):
        return len(self._turns)


class Watcher:
    def __init__(self, model="qwen2.5:7b", url="http://127.0.0.1:11434",
                 min_confidence=0.6, timeout=8.0, history=6, verbose=False,
                 backups=None):
        self.model = model
        self.url = url.rstrip("/")
        self.min_confidence = min_confidence
        self.timeout = timeout
        self.history = history
        self.verbose = verbose
        # Backup local models to try, in order, if the primary errors. All share
        # the same Ollama server; an uninstalled name just fails and is skipped.
        self.backups = [m for m in (backups or []) if m and m != model]

    def _models(self):
        """Models to attempt, primary first."""
        return [self.model] + list(self.backups)

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
            "You are also a GRADER/NORMALIZER: you translate the user's spoken "
            "words into the EXACT argument values the skill expects. The skills "
            "are dumb - they do not understand natural language, so YOU must "
            "convert. Never pass a natural-language phrase as an argument.\n\n"
            "Rules:\n"
            "- If the message maps to one or more skills, list them in `actions` "
            "with their args. Use intent 'skill' (only skills) or 'mixed' (skills "
            "plus something to say).\n"
            "- If it is only conversation, use intent 'chat' with no actions.\n"
            "- NORMALIZE args to machine form. For calculator 'expr': emit a plain "
            "arithmetic expression with digits and these only: + - * / ( ) **. "
            "Convert spoken words: times/multiplied by -> *, plus/add -> +, "
            "minus/take away -> -, divided by/over -> /, % -> %, 'squared' -> **2, "
            "'cubed' -> **3, 'to the Nth power' -> **N, and the symbols x/\u00d7 -> *, "
            "\u00f7 -> /. Example: '100\u00d73 to the third power' -> expr '100*3**3'.\n"
            "- For music 'volume': put a number 0-100 in `level` (louder->80, "
            "quieter->40, full/max->100, mute->0). Never pass words as the number.\n"
            "- For times: pass the literal phrase like 'in 20 minutes' / '3pm'; do "
            "NOT compute absolute datetimes yourself.\n"
            "- If a required arg is unknown, put its name in `missing` and do NOT guess.\n"
            "- If the whole request is ambiguous, use intent 'clarify' and put the "
            "question in `clarify`.\n"
            "- Set confidence 0..1. If unsure, prefer 'chat' with low confidence.\n"
            "- Set `chat` true if a spoken reply is still wanted (almost always true).\n"
            "- Earlier user turns may appear before the current one as context. \n"
            "  Use them ONLY to resolve pronouns/ellipsis (e.g. 'turn it down' -> \n"
            "  music volume, 'do it again' -> the previous skill). Do NOT act on a \n"
            "  previous request again; route only what the CURRENT turn asks. When \n"
            "  you lean on a prior turn, name it in `context_used`.\n\n"
            "Examples:\n"
            "  user: \"what's 47 times 89\" -> {\"intent\":\"skill\",\"confidence\":1,"
            "\"actions\":[{\"skill\":\"calculator\",\"args\":{\"expr\":\"47*89\"},"
            "\"on_error\":\"skip\"}],\"chat\":true}\n"
            "  user: \"what's 100\u00d73 to the third power\" -> {\"intent\":\"skill\","
            "\"confidence\":1,\"actions\":[{\"skill\":\"calculator\","
            "\"args\":{\"expr\":\"100*3**3\"},\"on_error\":\"skip\"}],\"chat\":true}\n"
            "  user: \"play my liked songs\" -> {\"intent\":\"skill\",\"confidence\":1,"
            "\"actions\":[{\"skill\":\"music\",\"args\":{\"command\":\"play\"},"
            "\"on_error\":\"skip\"}],\"chat\":true}\n"
            "  user: \"stop the music\" -> {\"intent\":\"skill\",\"confidence\":1,"
            "\"actions\":[{\"skill\":\"music\",\"args\":{\"command\":\"stop\"},"
            "\"on_error\":\"skip\"}],\"chat\":true}\n"
            "  user: \"turn the music up\" -> {\"intent\":\"skill\",\"confidence\":1,"
            "\"actions\":[{\"skill\":\"music\",\"args\":{\"command\":\"volume\","
            "\"level\":80},\"on_error\":\"skip\"}],\"chat\":true}\n"
            "  user: \"tell me a joke\" -> {\"intent\":\"chat\",\"confidence\":0.9,"
            "\"actions\":[],\"chat\":true}"
        )

    def _ask(self, model, msgs):
        """One constrained chat call. Returns the parsed route dict, or raises.

        Keeping this tiny and isolated makes the fallback loop trivial and means
        a model swap can never touch the surrounding logic.
        """
        body = {
            "model": model,
            "messages": msgs,
            "stream": False,
            "format": ROUTE_SCHEMA,
            "options": {"temperature": 0},
        }
        req = urllib.request.Request(
            self.url + "/api/chat", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            resp = json.loads(r.read().decode())
        content = (resp.get("message") or {}).get("content") or ""
        return json.loads(content)

    def route(self, transcript, skills, history=None):
        """Return a ROUTE dict. Always returns something usable.

        Tries the primary model, then each backup, and only degrades to a plain
        passthrough if every model errors (the safe invariant; see WATCHER.md).
        """
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

        data = None
        tried = []
        for model in self._models():
            tried.append(model)
            try:
                data = self._ask(model, msgs)
                break
            except Exception as e:
                if self.verbose:
                    print(f"[watcher] model {model} failed: {type(e).__name__}")
                continue
        if data is None:
            why = ("router error all models" if len(tried) > 1 else
                   "router error")
            return _passthrough(transcript, why)

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
