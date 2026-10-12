"""Unit tests proving extensions hot-reload without a pipeline restart.

The loader's `reload_tick()` re-scans `extensions/`; dropping, editing, or
removing a folder must be picked up on the next tick with no restart.
"""
import json
import os
import shutil
import textwrap

from sparkext.loader import Manager


def _write_ext(root, ext_id, version="0.1.0", enabled=True, body=None):
    d = os.path.join(root, ext_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "extension.json"), "w") as f:
        json.dump({"id": ext_id, "name": ext_id.title(), "version": version,
                   "description": "test ext", "author": "t", "entry": "extension.py",
                   "enabled": enabled, "scopes": ["log"], "hooks": ["tools"],
                   "config": {}}, f)
    body = body or textwrap.dedent("""
        def setup(ctx):
            def tools(ctx):
                return [{"name": "%s_say", "description": "echo",
                         "args": {}, "fn": lambda a, ctx: {"ok": True, "data": {}}}]
            return {"tools": tools}
    """ % ext_id)
    with open(os.path.join(d, "extension.py"), "w") as f:
        f.write(body)
    return d


def build(tmp_path):
    root = tmp_path / "extensions"
    data = tmp_path / "data"
    root.mkdir()
    mgr = Manager(root=str(root), data_root=str(data))
    return mgr, root


def test_dropping_a_folder_loads_without_restart(tmp_path):
    mgr, root = build(tmp_path)
    assert mgr.load_all(force=True) == 0

    _write_ext(root, "alpha")
    # a reload tick picks up the new folder, no restart
    assert mgr.reload_tick() == 1
    assert "alpha" in mgr.extensions
    assert mgr.extensions["alpha"].active


def test_no_change_is_a_noop(tmp_path):
    mgr, root = build(tmp_path)
    _write_ext(root, "alpha")
    mgr.reload_tick()
    # nothing changed -> the signature is identical, load_all returns 0
    assert mgr.reload_tick() == 0
    assert "alpha" in mgr.extensions


def test_editing_entry_reloads(tmp_path):
    mgr, root = build(tmp_path)
    d = _write_ext(root, "alpha")
    mgr.reload_tick()

    new_body = textwrap.dedent("""
        def setup(ctx):
            def tools(ctx):
                return [{"name": "alpha_v2", "description": "echo v2",
                         "args": {}, "fn": lambda a, ctx: {"ok": True, "data": {}}}]
            return {"tools": tools}
    """)
    # change mtime so the signature differs (mtime resolution can be coarse)
    with open(os.path.join(d, "extension.py"), "w") as f:
        f.write(new_body)
    os.utime(os.path.join(d, "extension.py"), None)

    mgr.reload_tick()
    names = [t["name"] for t in mgr.extensions["alpha"].hooks["tools"](
        mgr.extensions["alpha"].ctx)]
    assert names == ["alpha_v2"]


def test_removing_a_folder_unloads(tmp_path):
    mgr, root = build(tmp_path)
    d = _write_ext(root, "alpha")
    _write_ext(root, "beta")
    mgr.reload_tick()
    assert set(mgr.extensions) == {"alpha", "beta"}

    os.remove(os.path.join(d, "extension.py"))
    os.remove(os.path.join(d, "extension.json"))
    shutil.rmtree(d, ignore_errors=True)
    mgr.reload_tick()
    assert set(mgr.extensions) == {"beta"}


def test_disabled_folder_loads_inactive(tmp_path):
    mgr, root = build(tmp_path)
    _write_ext(root, "alpha", enabled=False)
    mgr.reload_tick()
    ext = mgr.extensions["alpha"]
    assert ext.id in mgr.extensions and not ext.active


def test_broken_extension_does_not_take_down_the_manager(tmp_path):
    mgr, root = build(tmp_path)
    _write_ext(root, "good")
    _write_ext(root, "bad", body="def setup(ctx):\n    raise RuntimeError('boom')\n")
    mgr.reload_tick()
    assert mgr.extensions["good"].active
    assert not mgr.extensions["bad"].active
    assert "boom" in (mgr.extensions["bad"].error or "")
