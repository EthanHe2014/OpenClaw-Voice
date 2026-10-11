"""Bridge — wires WATCHER -> RUNNER -> OpenClaw.

This is the new front of the pipeline:

    transcript
      -> watcher.route()        (LLM picks skills, emits a ROUTE)
      -> registry.run_route()   (python runs them, builds a RESULT)
      -> format_for_agent()     (original text + "handled" note + tool output)
      -> OpenClaw composes the spoken reply

Everything is guarded: if the watcher is offline or unsure it degrades to a
plain passthrough, i.e. exactly the old behaviour. See WATCHER.md.
"""
import json
import time


class Bridge:
    def __init__(self, manager, registry, watcher, log_path=None, emit=None):
        self.manager = manager
        self.registry = registry
        self.watcher = watcher
        self.log_path = log_path
        self.emit = emit

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
                    "notes": route.get("notes", ""),
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def process(self, transcript, history=None):
        """Full pass. Returns (agent_text, result, route)."""
        route = self.watcher.route(transcript, self.registry.catalog(),
                                   history=history)
        if route.get("intent") in ("chat", "noop") or not route.get("actions"):
            # nothing to run: hand the raw text straight to the agent
            return transcript, {"handled": []}, route
        result = self.registry.run_route(route, self.manager)
        self._log_route(route, result)
        if self.emit:
            try:
                if result.get("handled"):
                    self.emit("WATCHER", f"handled {result['handled']}")
                else:
                    self.emit("WATCHER", f"no skills ({route.get('intent')})")
            except Exception:
                pass
        return format_for_agent(transcript, result), result, route


def format_for_agent(user_text, result):
    """Render the message OpenClaw actually receives: original words + a
    'already handled, don't redo' note + the tool output."""
    handled = result.get("handled") or []
    if not handled:
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
    lines.append("[WATCHER] tool already run - its output: " + "; ".join(parts) + ".")
    lines.append("Do NOT call this tool again. YOU phrase the spoken reply yourself "
                 "(short, natural, 2-4 sentences), using this output; also handle "
                 "anything else the user asked.")
    if result.get("needs_confirmation"):
        lines.append("Some actions need the user's confirmation first - ask for it, "
                     "do not assume.")
    return "\n".join(lines)
