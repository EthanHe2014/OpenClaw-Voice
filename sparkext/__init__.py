"""Spark extensions — a tiny, dependency-free plugin framework.

An extension is a folder under `extensions/` with an `extension.json` manifest
and a Python entry file exposing `setup(ctx) -> {hook_name: callable}`.

The loader discovers, loads, dispatches, and hot-reloads extensions without ever
letting one crash the voice pipeline. See EXTENSIONS.md for the full contract.

The watcher stack (see WATCHER.md) sits on top:
  watcher.Watcher   -> LLM router, emits a ROUTE
  registry.Registry -> collects skills, runs a ROUTE, builds a RESULT
  bridge.Bridge     -> wires transcript -> route -> result -> OpenClaw handoff
"""

from .context import Context
from .loader import Extension, Manager, discover, load_extension
from .watcher import Watcher
from .registry import Registry, Skill
from .bridge import Bridge, format_for_agent

__all__ = ["Context", "Extension", "Manager", "discover", "load_extension",
           "Watcher", "Registry", "Skill", "Bridge", "format_for_agent"]
