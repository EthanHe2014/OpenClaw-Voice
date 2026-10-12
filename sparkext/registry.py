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


def _coerce_arg(vspec, name, value):
    """Enforce/coerce ONE declared arg type. Returns (ok, value, err).

    Types a skill may declare:
      - enum:     value must be in `values`
      - number:   int/float, or a numeric string (coerced)
      - duration: a phrase string, or a numeric amount of seconds
      - date:     a date/time phrase string
      - string:   anything (numbers are stringified)
    The TOOL stays dumb: the model normalizes to the right type, and the registry
    only rejects values that are clearly wrong so a skill never runs on garbage.
    """
    t = (vspec.get("type") or "string").lower()

    if t == "enum":
        values = vspec.get("values") or []
        if value not in values:
            return False, None, {"error": "invalid_args", "arg": name,
                                 "detail": f"{name!r} must be one of {values}"}
        return True, value, None

    if t == "number":
        if isinstance(value, bool):
            return False, None, {"error": "invalid_args", "arg": name,
                                 "detail": f"{name!r} must be a number"}
        if isinstance(value, (int, float)):
            return True, value, None
        if isinstance(value, str) and value.strip():
            try:
                return True, float(value.strip()), None
            except ValueError:
                pass
        return False, None, {"error": "invalid_args", "arg": name,
                             "detail": f"{name!r} must be a number"}

    if t == "duration":
        if isinstance(value, bool):
            value = None
        elif isinstance(value, (int, float)):
            return True, value, None  # seconds
        elif isinstance(value, str) and value.strip():
            return True, value, None  # phrase, parsed by the skill
        return False, None, {"error": "invalid_args", "arg": name,
                             "detail": f"{name!r} must be a duration phrase or seconds"}

    if t == "date":
        if isinstance(value, str) and value.strip():
            return True, value, None
        return False, None, {"error": "invalid_args", "arg": name,
                             "detail": f"{name!r} must be a date/time phrase"}

    if t in ("bool", "boolean"):
        if isinstance(value, bool):
            return True, value, None
        if isinstance(value, str) and value.strip().lower() in (
                "true", "false", "yes", "no", "1", "0"):
            return True, value.strip().lower() in ("true", "yes", "1"), None
        if isinstance(value, (int, float)):
            return True, bool(value), None
        return False, None, {"error": "invalid_args", "arg": name,
                             "detail": f"{name!r} must be true/false"}

    # default: string
    if isinstance(value, str):
        return True, value, None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True, str(value), None
    return False, None, {"error": "invalid_args", "arg": name,
                         "detail": f"{name!r} must be a string"}


def _validate_args(spec, args):
    """Return (ok, err, coerced). Missing required args are reported, not guessed;
    declared types (string/number/enum/date/duration) are enforced and numeric
    strings are coerced to numbers."""
    missing = [k for k, v in spec.items()
               if v.get("required") and k not in args]
    if missing:
        return False, {"error": "missing_args", "missing": missing}, None
    coerced = dict(args)
    for name, vspec in spec.items():
        if name not in args:
            continue
        ok, val, err = _coerce_arg(vspec, name, args[name])
        if not ok:
            return False, err, None
        coerced[name] = val
    return True, None, coerced


class Registry:
    def __init__(self, emit=None):
        self.skills = {}                # name -> Skill
        self.emit = emit                # optional dashboard event callback

    def register(self, skill):
        self.skills[skill.name] = skill

    def _event(self, kind, detail):
        """Push a dashboard event; never raises into the pipeline."""
        if not self.emit:
            return
        try:
            self.emit(kind, detail)
        except Exception:
            pass

    def _report(self, res):
        """Make a failed skill visible: a dashboard event + a returned field."""
        if not res.get("ok"):
            self._event("WATCHER",
                        f"skill failed: {res.get('skill')} ({res.get('error')})")
        return res

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
            res = {"skill": name, "ok": False, "error": "unknown_skill"}
        else:
            ok, err, coerced = _validate_args(sk.args, action.get("args") or {})
            if not ok:
                res = {"skill": name, "ok": False, "error": err["error"],
                       "missing": err.get("missing", []),
                       "arg": err.get("arg"), "detail": err.get("detail")}
            else:
                ctx = ctx_by_ext.get(sk.ext_id)
                try:
                    out = sk.fn(coerced, ctx) or {}
                    out = dict(out)
                    out.setdefault("skill", name)
                    out["ok"] = out.get("ok", True)
                    out["ms"] = int((time.time() - t0) * 1000)
                    res = out
                except Exception as e:
                    res = {"skill": name, "ok": False,
                           "error": f"{type(e).__name__}: {e}",
                           "ms": int((time.time() - t0) * 1000)}
        return self._report(res)

    def run_route(self, route, manager):
        """Execute every action in order. Returns the RESULT document."""
        ctx_by_ext = {e.id: e.ctx for e in manager.extensions.values()}
        results = []; handled = []
        confirm_pending = False
        for i, action in enumerate(route.get("actions", [])):
            if action.get("missing"):
                results.append(self._report({"skill": action["skill"], "ok": False,
                                             "error": "missing_args",
                                             "missing": action["missing"]}))
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
    failures = [{"skill": r.get("skill"), "error": r.get("error"),
                 "missing": r.get("missing"), "arg": r.get("arg"),
                 "detail": r.get("detail")}
                for r in results if not r.get("ok")]
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
        "failures": failures,
        "forward_to_agent": forward,
        "agent_note": note,
        "needs_confirmation": confirm_pending,
    }
