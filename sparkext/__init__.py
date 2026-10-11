"""Spark extensions — a tiny, dependency-free plugin framework.

An extension is a folder under `extensions/` with an `extension.json` manifest
and a Python entry file exposing `setup(ctx) -> {hook_name: callable}`.

The loader discovers, loads, dispatches, and hot-reloads extensions without ever
letting one crash the voice pipeline. See EXTENSIONS.md for the full contract.
"""

from .context import Context
from .loader import Extension, Manager, discover, load_extension

__all__ = ["Context", "Extension", "Manager", "discover", "load_extension"]
