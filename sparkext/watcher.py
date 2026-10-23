"""Watcher — the local LLM handler.

Sees the STT transcript (+ a little history), decides what should happen, and
emits a ROUTE (see WATCHER.md). It never runs a skill itself (the deterministic
runner does), but it MAY finish the turn: when the route fully covers what the
user asked, it writes its own spoken `reply` and OpenClaw is skipped entirely.

Brain: a local model via Ollama, called with a JSON-schema constraint so the
output physically cannot be malformed. On ANY problem (model down, timeout,
bad JSON, low confidence, no reply) it returns a plain PASSTHROUGH route — the
system then behaves exactly like before. This is the invariant that makes it safe.
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
        "answer": {"type": "boolean"},
        "notes": {"type": "string"}
    },
    "required": ["intent", "confidence", "actions", "chat"]
}


# The composer gets NO example replies: any example becomes a template the model
# parrots back. It is grounded in the real tool output and free to phrase it.
COMPOSE_SYSTEM = (
    "You are Spark, a warm, quick-witted voice assistant talking out loud to "
    "Ethan. You already ran his request; you are given the ACTUAL result.\n\n"
    "Write the single thing you will SAY to him, right now, as speech.\n\n"
    "Rules:\n"
    "- Speak naturally, the way a friend actually talks. Vary your wording; never "
    "sound like a template or a form letter.\n"
    "- Be brief: one or two sentences. This is out loud, not an essay.\n"
    "- Ground every fact in the result given to you. Never invent a number, time, "
    "place or outcome. If the result is thin, say less rather than guess.\n"
    "- Plain spoken English. No markdown, no emoji, no lists, no field names, no "
    "JSON, no skill names, no 'according to the tool'.\n"
    "- Do not mention that a tool or skill ran. Just tell him the thing.\n"
    "- If the result is a question you must ask him (missing information), ask it "
    "conversationally, in your own words.\n"
    "- Reply with the spoken words ONLY. No quotes, no preamble."
)

ASK_SYSTEM = (
    "You are Spark, a warm voice assistant talking out loud to Ethan.\n\n"
    "You were about to do what he asked, but you are missing something you need."
    "\n\nAsk him for it in your own words.\n\n"
    "Rules:\n"
    "- One short natural sentence, the way a friend would ask.\n"
    "- Ask only for what is actually missing. Do not guess an answer.\n"
    "- Do not say 'parameter', 'argument', 'required field' or any system word.\n"
    "- Plain spoken English, no markdown, no emoji, no quotes.\n"
    "- Reply with the spoken words ONLY."
)


def _passthrough(transcript, why):
    return {
        "v": 1, "id": "r-" + uuid.uuid4().hex[:6],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tz": time.strftime("%Z"), "transcript": transcript,
        "intent": "chat", "confidence": 0.0, "actions": [],
        "chat": True, "clarify": None, "answer": False,
        "notes": f"passthrough ({why})",
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


_RULES = (
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
        "- For music 'play': `target` may ONLY be filled from what the user "
        "actually named ('my liked songs' -> 'liked', a song or playlist they "
        "said). If they just said 'play some music' with no choice, set "
        "`missing`: [\"target\"] and do NOT invent one - never assume liked songs.\n"
        "- For weather: put a plain place name in `city` (e.g. 'Beijing', "
        "'Shanghai', 'San Francisco'); never pass a full sentence.\n"
        "- For times: pass the literal phrase like 'in 20 minutes' / '3pm'; do "
        "NOT compute absolute datetimes yourself.\n"
        "- If a required arg is unknown, put its name in `missing` and do NOT guess.\n"
        "- Set `answer` true when the available skills can FULLY satisfy the request "
        "(the result will be spoken to the user). Set `answer` false when the "
        "request needs anything the skills cannot do - general knowledge, jokes, "
        "opinions, reasoning, chit-chat - so the bigger assistant takes it.\n"
        "- When `answer` is true, also set `chat` false: the turn ends locally. "
        "When `answer` is false, set `chat` true: the turn escalates.\n"
        "- If the whole request is ambiguous, use intent 'clarify' and put the "
        "question in `clarify`.\n"
        "- Set confidence 0..1. If unsure, prefer 'chat' with low confidence.\n"
        "- `chat` and `answer` must agree: answer true -> chat false; answer false "
        "-> chat true.\n"
        "- Earlier user turns may appear before the current one as context. \n"
        "  Use them ONLY to resolve pronouns/ellipsis (e.g. 'turn it down' -> \n"
        "  music volume, 'do it again' -> the previous skill). Do NOT act on a \n"
        "  previous request again; route only what the CURRENT turn asks. When \n"
        "  you lean on a prior turn, name it in `context_used`.\n\n"
        "Examples:\n"
        "  user: \"what's 47 times 89\" -> {\"intent\":\"skill\",\"confidence\":1,"
        "\"actions\":[{\"skill\":\"calculator\",\"args\":{\"expr\":\"47*89\"},"
        "\"on_error\":\"skip\"}],\"chat\":false,\"answer\":true}\n"
        "  user: \"play some music\" (target missing) -> {\"intent\":\"clarify\","
        "\"confidence\":1,\"actions\":[{\"skill\":\"music\","
        "\"args\":{\"command\":\"play\"},\"missing\":[\"target\"]}],"
        "\"chat\":false,\"answer\":true}\n"
        "  user: \"what's 100\u00d73 to the third power\" -> {\"intent\":\"skill\","
        "\"confidence\":1,\"actions\":[{\"skill\":\"calculator\","
        "\"args\":{\"expr\":\"100*3**3\"},\"on_error\":\"skip\"}],"
        "\"chat\":false,\"answer\":true}\n"
        "  user: \"play my liked songs\" -> {\"intent\":\"skill\",\"confidence\":1,"
        "\"actions\":[{\"skill\":\"music\",\"args\":{\"command\":\"play\","
        "\"target\":\"liked\"},\"on_error\":\"skip\"}],\"chat\":false,"
        "\"answer\":true}\n"
        "  user: \"stop the music\" -> {\"intent\":\"skill\",\"confidence\":1,"
        "\"actions\":[{\"skill\":\"music\",\"args\":{\"command\":\"stop\"},"
        "\"on_error\":\"skip\"}],\"chat\":false,\"answer\":true}\n"
        "  user: \"turn the music up\" -> {\"intent\":\"skill\",\"confidence\":1,"
        "\"actions\":[{\"skill\":\"music\",\"args\":{\"command\":\"volume\","
        "\"level\":80},\"on_error\":\"skip\"}],\"chat\":false,"
        "\"answer\":true}\n"
        "  user: \"what's the weather in Beijing\" -> {\"intent\":\"skill\","
        "\"confidence\":1,\"actions\":[{\"skill\":\"weather\","
        "\"args\":{\"city\":\"Beijing\"},\"on_error\":\"skip\"}],"
        "\"chat\":false,\"answer\":true}\n"
        "  user: \"tell me a joke\" -> {\"intent\":\"chat\",\"confidence\":0.9,"
        "\"actions\":[],\"chat\":true}"
)


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

    def _system_prompt(self, skills, now=None, tz=None):
        """Build the routing prompt.

        The catalog and rules are the slow, cacheable part, so they are kept
        byte-identical between turns. The clock is NOT embedded: it changes every
        minute and would invalidate Ollama's prefix cache, turning a ~3s call into
        a ~20s one (measured). Skills that need the time call `ctx.now()`
        themselves, and relative phrases are resolved downstream.
        """
        head = (
            "You are the ROUTER for a voice assistant. You decide what should "
            "happen: which skills to run, with which arguments. You do NOT write "
            "the spoken answer - a separate step phrases it from the real result.\n\n"
            "Available skills:\n" + self._catalog(skills) + "\n\n"
        )
        return head + _RULES

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
            # Keep the model resident. Ollama unloads after ~5 min idle, and a cold
            # load plus a fresh ~2k-token prefix costs ~20s (measured) versus ~3s
            # warm. This is what made every turn appear to "go to OpenClaw".
            "keep_alive": "12h",
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
            # Label prior turns explicitly. Unlabelled user messages make a small
            # model treat them as part of the current request and re-run their
            # skills (observed: actions accumulating turn after turn).
            msgs.append({"role": "user", "content":
                         f"[earlier turn, already answered - do NOT repeat or act on "
                         f"this] {turn}"})
            msgs.append({"role": "assistant", "content":
                         "[noted as past context only]"})
        msgs.append({"role": "user", "content": transcript})

        data = None
        tried = []
        last_err = None
        for model in self._models():
            tried.append(model)
            try:
                data = self._ask(model, msgs)
                break
            except Exception as e:
                last_err = e
                if self.verbose:
                    print(f"[watcher] model {model} failed: {type(e).__name__}")
                continue
        if data is None:
            why = ("router error all models" if len(tried) > 1 else
                   "router error")
            # Name the real cause. A bare "passthrough" hides a timeout behind
            # silence, which is how the 15s-vs-21s bug went unnoticed for days.
            note = f"passthrough ({why}: {type(last_err).__name__})" \
                if last_err else f"passthrough ({why})"
            r = _passthrough(transcript, note)
            r["notes"] = note
            return r

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

        # True when the local skills can fully satisfy the turn; the composer then
        # phrases the REAL tool output and the agent is skipped.
        answer = bool(data.get("answer", False))
        chat = bool(data.get("chat", True))
        if answer:
            chat = False          # answer and chat are two sides of one switch

        return {
            "v": 1, "id": "r-" + uuid.uuid4().hex[:6],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tz": time.strftime("%Z"), "transcript": transcript,
            "intent": intent, "confidence": round(conf, 3),
            "actions": actions, "chat": chat,
            "clarify": data.get("clarify"),
            "answer": answer,
            "notes": data.get("notes", ""),
        }

    # --- speaking: phrase the REAL outcome, never a template -----------------
    def _plain(self, text):
        """Take the model's spoken line and strip anything that is not speech."""
        t = (text or "").strip()
        if not t:
            return None
        # A model asked for speech sometimes still wraps it in quotes.
        if len(t) > 1 and t[0] in "\"\u201c'" and t[-1] in "\"\u201d'":
            t = t[1:-1].strip()
        t = t.replace("**", "").replace("`", "")
        if not t:
            return None
        # Reject obvious non-speech (JSON, lists, field names) rather than read it.
        if t.startswith("{") or t.startswith("["):
            return None
        if "\n" in t:
            t = " ".join(part.strip() for part in t.splitlines() if part.strip())
        return t or None

    def compose(self, transcript, result):
        """Speak the REAL tool output. Returns None if the model cannot.

        This runs AFTER skills executed, so it phrases actual data instead of
        guessing. No example replies are provided anywhere: an example becomes a
        template that a small model parrots back.
        """
        parts = []
        for r in result.get("results") or []:
            if not r.get("ok"):
                continue
            out = r.get("speak")
            if out is None:
                out = r.get("data")
            if out is None:
                out = "done"
            if isinstance(out, (dict, list)):
                out = json.dumps(out, ensure_ascii=False)
            parts.append(f"{r['skill']}: {out}")
        if not parts:
            return None
        user = (f"Ethan said: {transcript}\n\n"
                f"The result you got:\n" + "\n".join(parts) +
                "\n\nNow say it to him out loud.")
        msgs = [{"role": "system", "content": COMPOSE_SYSTEM},
                {"role": "user", "content": user}]
        for model in self._models():
            try:
                return self._plain(self._chat_text(model, msgs))
            except Exception as e:
                if self.verbose:
                    print(f"[watcher] compose {model} failed: {type(e).__name__}")
        return None

    def ask(self, transcript, missing):
        """Ask the user for exactly what is missing, in the model's own words."""
        user = (f"Ethan said: {transcript}\n\n"
                f"You still need: {', '.join(missing)}\n\n"
                "Ask him for it.")
        msgs = [{"role": "system", "content": ASK_SYSTEM},
                {"role": "user", "content": user}]
        for model in self._models():
            try:
                return self._plain(self._chat_text(model, msgs))
            except Exception as e:
                if self.verbose:
                    print(f"[watcher] ask {model} failed: {type(e).__name__}")
        return None

    def _chat_text(self, model, msgs):
        """Free-form (unconstrained) chat call returning the raw text."""
        body = {"model": model, "messages": msgs, "stream": False,
                "options": {"temperature": 0.7}, "keep_alive": "12h"}
        req = urllib.request.Request(
            self.url + "/api/chat", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            resp = json.loads(r.read().decode())
        return (resp.get("message") or {}).get("content") or ""
