"""Extension discovery, loading, and guarded hook dispatch.

Failures are contained: a broken extension is disabled with a reason, and hook
calls are wrapped so one bad extension can never kill the voice pipeline.
"""
import os
import sys
import json
import time
import importlib.util
import threading
import traceback

from .context import Context

MANIFEST = "extension.json"
HOOKS = (
    "on_startup", "on_shutdown", "on_transcript", "on_reply", "on_speak",
    "on_task_created", "tools",
)


class Extension:
    def __init__(self, path, manifest):
        self.path = path
        self.manifest = manifest
        self.id = manifest.get("id") or os.path.basename(path)
        self.name = manifest.get("name", self.id)
        self.version = manifest.get("version", "0.0.0")
        self.description = manifest.get("description", "")
        self.author = manifest.get("author", "")
        self.scopes = manifest.get("scopes")  # None = unrestricted
        self.config = manifest.get("config", {}) or {}
        self.enabled = bool(manifest.get("enabled", True))
        self.hooks = {}          # name -> callable
        self.ctx = None
        self.error = None

    @property
    def active(self):
        return self.enabled and self.error is None and bool(self.hooks)


def discover(root):
    """Yield extension folder paths that contain a manifest."""
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if os.path.isdir(d) and os.path.exists(os.path.join(d, MANIFEST)):
            out.append(d)
    return out


def _read_manifest(path):
    with open(os.path.join(path, MANIFEST)) as f:
        return json.load(f) or {}


def _load_module(entry_path, ext_id):
    mod_name = f"sparkext_ext_{ext_id}_{int(time.time()*1000)}"
    spec = importlib.util.spec_from_file_location(mod_name, entry_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_extension(path, data_root, host=None, adapters=None, emit=None):
    """Load one extension folder. Returns an Extension (possibly failed)."""
    def _emit(kind, detail):
        if emit:
            try:
                emit(kind, detail)
            except Exception:
                pass

    try:
        manifest = _read_manifest(path)
    except Exception as e:
        ext = Extension(path, {"id": os.path.basename(path)})
        ext.error = f"bad manifest: {e}"
        _emit("ERROR", f"extension {ext.id}: {ext.error}")
        return ext

    ext = Extension(path, manifest)
    data_dir = os.path.join(data_root, ext.id)
    os.makedirs(data_dir, exist_ok=True)

    ext.ctx = Context(ext.id, ext.name, ext.config, ext.scopes, data_dir,
                      host=host, adapters=adapters)

    if not ext.enabled:
        _emit("INFO", f"extension {ext.id} disabled")
        return ext

    entry = manifest.get("entry", "extension.py")
    entry_path = os.path.join(path, entry)
    if not os.path.exists(entry_path):
        ext.error = f"missing entry {entry}"
        _emit("ERROR", f"extension {ext.id}: {ext.error}")
        return ext

    try:
        mod = _load_module(entry_path, ext.id)
        setup = getattr(mod, "setup", None)
        if not callable(setup):
            raise RuntimeError("no setup(ctx) function")
        returned = setup(ext.ctx) or {}
        # Keep only known hook names that are callable.
        ext.hooks = {k: v for k, v in returned.items()
                     if k in HOOKS and callable(v)}
        unknown = [k for k in returned if k not in HOOKS]
        if unknown:
            _emit("WARN", f"extension {ext.id}: unknown hooks ignored: {unknown}")
        if "on_startup" in ext.hooks:
            _safe_call(ext, "on_startup", ext.ctx)
        _emit("INFO", f"extension {ext.id} v{ext.version} loaded "
                      f"({len(ext.hooks)} hooks)")
    except Exception as e:
        ext.error = f"{type(e).__name__}: {e}"
        _emit("ERROR", f"extension {ext.id} failed to load: {ext.error}")
        _emit("INFO", traceback.format_exc().splitlines()[-1][:160])

    return ext


def _safe_call(ext, hook, *args):
    """Call one hook, never raising into the pipeline. Returns result or None."""
    fn = ext.hooks.get(hook)
    if not fn:
        return None
    try:
        return fn(*args)
    except Exception as e:
        short = f"{type(e).__name__}: {e}"
        ext.ctx.emit("ERROR", f"[{ext.id}] {hook} raised: {short}")
        return None


class Manager:
    """Owns the set of loaded extensions and dispatches hooks to them."""

    def __init__(self, root, data_root, host=None, adapters=None, emit=None):
        self.root = root
        self.data_root = data_root
        self.host = host or {}
        self.adapters = adapters or {}
        self.emit = emit
        self.extensions = {}         # id -> Extension
        self._sig = None             # hot-reload signature
        self._lock = threading.Lock()

    # -- discovery & hot reload -------------------------------------------
    def _signature(self):
        sig = []
        for d in discover(self.root):
            try:
                mt = os.path.getmtime(os.path.join(d, MANIFEST))
                mtc = os.path.getmtime(os.path.join(
                    d, _read_manifest(d).get("entry", "extension.py"))) \
                    if os.path.exists(os.path.join(
                        d, _read_manifest(d).get("entry", "extension.py"))) else 0
            except Exception:
                mt, mtc = 0, 0
            sig.append(f"{d}:{mt}:{mtc}")
        return "|".join(sig)

    def load_all(self, force=False):
        with self._lock:
            sig = self._signature()
            if not force and sig == self._sig:
                return 0
            self._sig = sig
            seen = set()
            for path in discover(self.root):
                ext = load_extension(path, self.data_root, host=self.host,
                                     adapters=self.adapters, emit=self.emit)
                seen.add(ext.id)
                self.extensions[ext.id] = ext
            for gone in set(self.extensions) - seen:
                self._unload(gone)
            return len(seen)

    def _unload(self, ext_id):
        ext = self.extensions.pop(ext_id, None)
        if ext and "on_shutdown" in ext.hooks:
            _safe_call(ext, "on_shutdown", ext.ctx)

    def reload_tick(self):
        """Called on a timer by the host; hot-loads folder changes."""
        return self.load_all(force=False)

    # -- dispatch ----------------------------------------------------------
    def dispatch_text(self, hook, text, *extra):
        """Run a text-transform hook across extensions in load order.
        Each may return a replacement string; the last non-None wins."""
        out = text
        for ext in list(self.extensions.values()):
            if not ext.active or hook not in ext.hooks:
                continue
            res = _safe_call(ext, hook, out, ext.ctx, *extra)
            if isinstance(res, str):
                out = res
        return out

    def dispatch(self, hook, *args):
        for ext in list(self.extensions.values()):
            if not ext.active or hook not in ext.hooks:
                continue
            _safe_call(ext, hook, *args, ext.ctx)

    def collect_routes(self):
        """Return {full_path: handler} for every extension-declared route."""
        routes = {}
        for ext in self.extensions.values():
            if not ext.active:
                continue
            for path, handler in ext.ctx.drain_routes().items():
                routes[f"/ext/{ext.id}/{path}"] = handler
        return routes

    def status(self):
        return [{
            "id": e.id, "name": e.name, "version": e.version,
            "enabled": e.enabled, "active": e.active,
            "scopes": e.scopes, "hooks": sorted(e.hooks.keys()),
            "error": e.error, "description": e.description,
        } for e in self.extensions.values()]
