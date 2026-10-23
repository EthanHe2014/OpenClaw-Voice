"""Bridge — wires WATCHER -> RUNNER -> OpenClaw.

This is the new front of the pipeline:

    transcript
      -> watcher.route()        (LLM picks skills, emits a ROUTE)
      -> registry.run_route()   (python runs them, builds a RESULT)
      -> format_for_agent()     (original text + "handled" note + tool output)
      -> OpenClaw composes the spoken reply

Everything is guarded: if the watcher is offline or unsure it degrades to a
plain passthrough, i.e. exactly the old behaviour. See WATCHER.md.

Confirm gate: a route action marked `confirm: true` is NOT executed by the
runner; it returns `needs_confirmation`. The bridge remembers the blocked route
and, on the NEXT utterance, runs it only if the user said yes (and drops it on a
no). This keeps the gate deterministic and the tools dumb.
"""
import json
import re
import time

from .watcher import TurnBuffer


# Short affirmative / negative replies that resolve a pending confirmation.
# Deterministic on purpose: the runner gates on the words, not on the model.
_AFFIRM = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "k",
           "do it", "go ahead", "go for it", "confirm", "confirmed",
           "please do", "yes please", "affirmative", "y", "sure thing"}
_NEGATE = {"no", "nope", "nah", "cancel", "stop", "dont", "do not",
           "negative", "never mind", "nevermind", "no thanks", "abort", "n"}
_AFFIRM_HEAD = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "y",
                "confirm", "affirmative"}
_NEGATE_HEAD = {"no", "nope", "nah", "cancel", "stop", "dont", "abort", "n"}


def _norm(text):
    """Lowercase, drop punctuation except apostrophes, collapse spaces."""
    return re.sub(r"[^a-z0-9' ]", "", (text or "").lower()).strip()


def _confirmation_verdict(text):
    """Classify a short reply as 'yes' / 'no' / None (the user moved on)."""
    t = _norm(text)
    if not t:
        return None
    if t in _AFFIRM:
        return "yes"
    if t in _NEGATE:
        return "no"
    words = t.split()
    if len(words) <= 3:
        if words[0] in _AFFIRM_HEAD:
            return "yes"
        if words[0] in _NEGATE_HEAD:
            return "no"
    return None


class Bridge:
    def __init__(self, manager, registry, watcher, log_path=None, emit=None,
                 history_size=6):
        self.manager = manager
        self.registry = registry
        self.watcher = watcher
        self.log_path = log_path
        self.emit = emit
        self._pending = None   # route blocked on confirmation, awaiting yes/no
        # Last few user turns, so the watcher can resolve pronouns ("turn it
        # down"). Bounded + stdlib; if it is ever empty the watcher just sees the
        # current turn, i.e. exactly the pre-context-window behaviour.
        self.turns = TurnBuffer(history_size)

    def _log_route(self, route, result):
        if not self.log_path:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "t": time.time(),
                    "isa": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "text": route.get("transcript", ""),
                    "intent": route.get("intent"),
                    "confidence": route.get("confidence"),
                    "actions": [a.get("skill") for a in route.get("actions", [])],
                    "handled": result.get("handled"),
                    "failures": [f.get("skill") for f in result.get("failures", [])],
                    "notes": route.get("notes", ""),
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _emit_result(self, result, route):
        if not self.emit:
            return
        try:
            if result.get("handled"):
                self.emit("WATCHER", f"handled {result['handled']}")
            elif result.get("needs_confirmation"):
                self.emit("WATCHER", "awaiting confirmation")
            elif not result.get("failures"):
                self.emit("WATCHER", f"no skills ({route.get('intent')})")
        except Exception:
            pass

    def _collect_pending(self, route):
        """Remember which actions were blocked by the confirm gate."""
        idxs = [i for i, a in enumerate(route.get("actions", []))
                if a.get("confirm")]
        return {"route": route, "indices": idxs} if idxs else None

    def _run_confirmed(self, pending):
        """User said yes: run ONLY the previously-blocked actions, confirm
        cleared, under a fresh route id so dedup does not suppress them."""
        src = pending["route"]
        actions = []
        for i in pending["indices"]:
            a = dict(src["actions"][i])
            a["confirm"] = False
            actions.append(a)
        route = {"id": (src.get("id") or "r") + "-confirmed",
                 "transcript": src.get("transcript", ""),
                 "intent": "skill", "actions": actions, "chat": True}
        result = self.registry.run_route(route, self.manager)
        return result, route

    def process(self, transcript, history=None):
        """Full pass. Returns (agent_text, result, route).

        `agent_text` is "" when the local model handled the turn itself; the
        spoken words are then in `result["local_reply"]` and OpenClaw is skipped.

        `history` may be passed explicitly (tests); otherwise the bridge's own
        bounded turn buffer is used, so pronouns resolve across live turns.
        """
        text = (transcript or "").strip()
        ctx_history = history if history is not None else self.turns.recent()
        # --- resolve a pending confirmation first ---
        if self._pending:
            verdict = _confirmation_verdict(text)
            if verdict == "yes":
                pending = self._pending
                self._pending = None
                self.turns.add(text)
                result, route = self._run_confirmed(pending)
                self._log_route(route, result)
                self._emit_result(result, route)
                return format_for_agent(text, result), result, route
            if verdict == "no":
                self._pending = None
                self.turns.add(text)
                result = {"handled": [], "results": [], "failures": [],
                          "forward_to_agent": True, "needs_confirmation": False}
                note = ("\n\n[WATCHER] the user DECLINED a pending action. Do not "
                        "run it. Acknowledge briefly that you will not do it.")
                return text + note, result, {"intent": "chat", "transcript": text}
            # anything else: the user moved on; drop the pending route
            self._pending = None
        route = self.watcher.route(text, self.registry.catalog(),
                                   history=ctx_history)
        # Record the turn for the NEXT one (after routing, so the current turn is
        # never duplicated into its own history).
        self.turns.add(text)

        # --- local handler: the model chose to finish the turn here ----------
        # Nothing runnable but the model wants to speak: it must be asking for
        # missing information (the only local answer possible without a skill).
        if not route.get("actions"):
            if route.get("answer"):
                missing = route.get("missing") or []
                reply = self.watcher.ask(text, missing or ["what it needs"]) \
                    if hasattr(self.watcher, "ask") else None
                if reply:
                    result = {"handled": [], "results": [], "failures": [],
                              "forward_to_agent": False,
                              "needs_confirmation": False,
                              "local_reply": reply,
                              "agent_note": "asked locally - no agent needed"}
                    self._log_route(route, result)
                    self._emit_result(result, route)
                    return "", result, route
            # nothing to run and nothing to say: hand the raw text to the agent
            self._log_route(route, {"handled": []})
            self._emit_result({"handled": [], "failures": []}, route)
            return transcript, {"handled": []}, route

        result = self.registry.run_route(route, self.manager)
        self._log_route(route, result)
        if result.get("needs_confirmation"):
            self._pending = self._collect_pending(route)
        self._emit_result(result, route)

        # --- compose from the REAL output ------------------------------------
        # The model never writes the answer before the skills run; it would have
        # to guess. Now that actual data exists, let it phrase that - unless a
        # hard failure occurred (then the agent gets the turn so the user is told
        # the truth) or the route chose to escalate.
        hard = [f for f in (result.get("failures") or [])
                if f.get("error") != "missing_args"]
        if route.get("answer") and not hard and not result.get("needs_confirmation"):
            if not result.get("handled"):
                # a skill refused for missing args: ask instead of running
                missing = []
                for f in (result.get("failures") or []):
                    missing += (f.get("missing") or [])
                reply = (self.watcher.ask(text, missing or ["what it needs"])
                         if hasattr(self.watcher, "ask") else None)
            else:
                reply = (self.watcher.compose(text, result)
                         if hasattr(self.watcher, "compose") else None)
            if reply:
                result["local_reply"] = reply
                result["forward_to_agent"] = False
                result["agent_note"] = "handled locally - no agent needed"
                return "", result, route

        return format_for_agent(transcript, result), result, route


def format_for_agent(user_text, result):
    """Render the message OpenClaw actually receives: original words + a
    'already handled, don't redo' note + the tool output."""
    handled = result.get("handled") or []
    failed = [f for f in result.get("failures", []) if f.get("error")
              not in (None, "needs_confirmation")]
    if not handled and not failed and not result.get("needs_confirmation"):
        return user_text
    lines = [user_text, ""]
    parts = []
    for r in result.get("results", []):
        if not r.get("ok"):
            continue
        out = r.get("speak")
        if out is None:
            out = r.get("data")
        parts.append(f"{r['skill']} -> {json.dumps(out, ensure_ascii=False)}"
                     if isinstance(out, (dict, list)) else f"{r['skill']} -> {out}")
    if parts:
        lines.append("[WATCHER] tool already run - its output: " + "; ".join(parts) + ".")
        lines.append("Do NOT call this tool again. YOU phrase the spoken reply yourself "
                     "(short, natural, 2-4 sentences), using this output; also handle "
                     "anything else the user asked.")
    if failed:
        detail = ", ".join(f"{f.get('skill')} ({f.get('error')})" for f in failed)
        lines.append(f"[WATCHER] some tools FAILED and produced no output: {detail}. "
                     "Tell the user briefly and, if useful, handle it yourself.")
    if result.get("needs_confirmation"):
        lines.append("Some actions need the user's confirmation first - ask for it, "
                     "do not assume.")
    return "\n".join(lines)
