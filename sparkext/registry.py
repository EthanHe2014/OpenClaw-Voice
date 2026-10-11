"""Skill registry — the watcher's catalog and the runner's dispatcher.

An extension may expose callable *skills* (distinct from hooks). A skill is a
named function with a declared arg schema. The registry collects them from all
loaded extensions, feeds the catalog to the watcher, and dispatches a route's
actions to the right function.
"""
import time


class Skill:
    def __init__(self, name, fn, description="", args=None, ext_id="", speak=False):
        self.name = name
        self.fn = fn                    # fn(args: dict, ctx) -> dict
        self.description = description
        self.args = args or {}          # {arg: {"type":..., "required":bool}}
        self.ext_id = ext_id
        self.speak = speak              # default: skill output is spoken?

    def catalog_entry(self):
        return {"name": self.name, "description": self.description,
                "args": self.args}


def _validate_args(spec, args):
    """Return (ok, err). Missing required args are reported, not guessed."""
    missing = [k for k, v in spec.items()
               if v.get("required") and k not in args]
    if missing:
        return False, {"error": "missing_args", "missing": missing}
    return True, None


class Registry:
    def __init__(self):
        self.skills = {}                # name -> Skill

    def register(self, skill):
        self.skills[skill.name] = skill

    def collect_from(self, manager):
        """Pull each extension's declared skills (via its `skills` hook)."""
        for ext in manager.extensions.values():
            if not ext.active:
                continue
            decls = ext.hooks.get("tools")
            if not decls:
                continue
            try:
                items = decls(ext.ctx) or []
            except Exception as e:
                ext.ctx.emit("ERROR", f"[{ext.id}] skills decl failed: {e}")
                continue
            for it in items:
                if not callable(it.get("fn")):
                    continue
                self.register(Skill(
                    name=it.get("name") or f"{ext.id}_skill",
                    fn=it["fn"], description=it.get("description", ""),
                    args=it.get("args", {}), ext_id=ext.id,
                    speak=bool(it.get("speak", False))))
        return self

    def catalog(self):
        return [s.catalog_entry() for s in self.skills.values()]

    def run_one(self, action, ctx_by_ext):
        """Execute one action. Returns a result dict (never raises)."""
        name = action.get("skill")
        sk = self.skills.get(name)
        t0 = time.time()
        if sk is None:
            return {"skill": name, "ok": False, "error": "unknown_skill"}
        ok, err = _validate_args(sk.args, action.get("args") or {})
        if not ok:
            return {"skill": name, "ok": False, "error": err["error"],
                    "missing": err.get("missing", [])}
        ctx = ctx_by_ext.get(sk.ext_id)
        try:
            out = sk.fn(action.get("args") or {}, ctx) or {}
            out = dict(out)
            out.setdefault("skill", name)
            out["ok"] = out.get("ok", True)
            out["ms"] = int((time.time() - t0) * 1000)
            return out
        except Exception as e:
            return {"skill": name, "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "ms": int((time.time() - t0) * 1000)}

    def run_route(self, route, manager):
        """Execute every action in order. Returns the RESULT document."""
        ctx_by_ext = {e.id: e.ctx for e in manager.extensions.values()}
        results = []; handled = []
        confirm_pending = False
        for i, action in enumerate(route.get("actions", [])):
            if action.get("missing"):
                results.append({"skill": action["skill"], "ok": False,
                                "error": "missing_args",
                                "missing": action["missing"]})
                continue
            if action.get("confirm"):
                confirm_pending = True
                results.append({"skill": action["skill"], "ok": False,
                                "error": "needs_confirmation",
                                "data": action.get("args")})
                continue
            res = self.run_one(action, ctx_by_ext)
            results.append(res)
            if res.get("ok"):
                handled.append(res["skill"])
            elif action.get("on_error") == "abort" and action.get("critical"):
                break
        return _build_result(route, results, handled, confirm_pending)


def _build_result(route, results, handled, confirm_pending):
    has_output = any(r.get("speak") for r in results if r.get("ok"))
    if handled:
        bits = []
        for r in results:
            if r.get("ok"):
                val = r.get("speak") or r.get("data") or "done"
                bits.append(f"{r['skill']} -> {val}")
        note = ("The watcher already handled: " + "; ".join(bits) +
                ". Do NOT redo these. Compose ONE short spoken reply"
                + (", and handle anything else the user asked." if route.get("chat") else "."))
    else:
        note = "The watcher ran nothing; answer the user normally."
    forward = bool(route.get("chat")) or (not has_output)
    return {
        "v": 1, "id": route.get("id"),
        "user_text": route.get("transcript", ""),
        "results": results, "handled": handled,
        "forward_to_agent": forward,
        "agent_note": note,
        "needs_confirmation": confirm_pending,
    }
