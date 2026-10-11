"""The Context object handed to every extension.

Extensions interact with Spark ONLY through this object, and only within the
capability scopes their manifest declares. Anything outside scope is a logged
no-op, never a crash.
"""
import os
import time
import json
import queue


class Context:
    def __init__(self, ext_id, name, config, scopes, data_dir, host=None,
                 adapters=None):
        self.id = ext_id
        self.name = name
        self.config = config or {}
        # If scopes is None the extension is unrestricted (trusted, built-in).
        self.scopes = None if scopes is None else set(scopes)
        self.data_dir = data_dir
        self._host = host            # callbacks into the live pipeline (optional)
        self._adapters = adapters or {}
        self._state_path = os.path.join(data_dir, "state.json")
        self._state = self._load_state()
        self._routes = {}            # path -> handler (drained by the host)
        self._outbox = queue.Queue() # speak/event requests when no host bound

    # -- scope check -------------------------------------------------------
    def _allowed(self, scope):
        return self.scopes is None or scope in self.scopes

    def _refuse(self, scope):
        self.emit("WARN", f"[{self.id}] scope '{scope}' not granted - ignored")

    # -- identity / logging ------------------------------------------------
    def log(self, msg):
        self.emit("INFO", f"[{self.id}] {msg}")

    def emit(self, kind, detail):
        # Events always allowed (they are the diagnostic surface); 'log' scope
        # gates the informational ones.
        cb = self._host.get("emit") if self._host else None
        if cb:
            try:
                cb(kind, detail)
            except Exception:
                pass
        else:
            self._outbox.put(("emit", (kind, detail)))

    # -- time --------------------------------------------------------------
    def now(self, fmt="%Y-%m-%d %H:%M:%S"):
        return time.strftime(fmt)

    def epoch(self):
        return time.time()

    # -- speech ------------------------------------------------------------
    def speak(self, text):
        if not self._allowed("speak"):
            return self._refuse("speak")
        cb = self._host.get("speak") if self._host else None
        if cb:
            try:
                return cb(text)
            except Exception as e:
                self.emit("ERROR", f"[{self.id}] speak: {e}")
        else:
            self._outbox.put(("speak", (text,)))

    # -- agent -------------------------------------------------------------
    def ask_agent(self, text):
        if not self._allowed("agent"):
            return self._refuse("agent")
        cb = self._host.get("ask_agent") if self._host else None
        if cb:
            try:
                return cb(text)
            except Exception as e:
                self.emit("ERROR", f"[{self.id}] ask_agent: {e}")
                return ""
        return ""

    # -- tasks -------------------------------------------------------------
    def create_task(self, content, when, speak=None, recurring=None,
                    priority="medium"):
        """Schedule a reminder. `when` is absolute local 'YYYY-MM-DD HH:MM'."""
        if not self._allowed("tasks"):
            return self._refuse("tasks")
        cb = self._host.get("create_task") if self._host else None
        if cb:
            try:
                return cb(content, when, speak, recurring, priority)
            except Exception as e:
                self.emit("ERROR", f"[{self.id}] create_task: {e}")
                return None
        return None

    # -- dashboard routes --------------------------------------------------
    def register_route(self, path, handler):
        """Expose GET /ext/<id>/<path> on the dashboard server."""
        self._routes[path.lstrip("/")] = handler

    def drain_routes(self):
        r = dict(self._routes)
        self._routes.clear()
        return r

    # -- per-extension state ----------------------------------------------
    def get_state(self, key, default=None):
        return self._state.get(key, default)

    def set_state(self, key, value):
        self._state[key] = value
        self._save_state()

    def _load_state(self):
        try:
            with open(self._state_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self):
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            tmp = self._state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._state, f, indent=2)
            os.replace(tmp, self._state_path)
        except Exception:
            pass
