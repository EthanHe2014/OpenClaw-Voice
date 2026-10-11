"""Todo — pure tool extension.

Exposes ONE tool: `todo`. It keeps a local to-do list in a JSON file and returns
RESULT DATA. It does NOT speak and does NOT consume the turn — the watcher routes
to it, then OpenClaw phrases the spoken reply (TTS reads that).

The MODEL interprets language and fills `command` (add/list/done) and `item`.
This tool stays dumb: it just mutates/reads a list stored in its own
`ctx.data_dir` (no extra scope needed).
"""
import os
import json


def _path(ctx):
    return os.path.join(ctx.data_dir, "todos.json")


def _load(ctx):
    try:
        with open(_path(ctx), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(ctx, items):
    os.makedirs(ctx.data_dir, exist_ok=True)
    p = _path(ctx)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)
    os.replace(tmp, p)


def _find(items, needle):
    """Return the index of the best-matching item (exact > substring)."""
    needle = (needle or "").strip().lower()
    if not needle:
        return None
    for i, it in enumerate(items):
        if it.get("text", "").strip().lower() == needle:
            return i
    for i, it in enumerate(items):
        if needle in it.get("text", "").strip().lower():
            return i
    return None


def setup(ctx):
    ctx.log("todo ready")

    def do_todo(args, ctx):
        cmd = (args.get("command") or "list").strip().lower()
        item = (args.get("item") or "").strip()

        if cmd == "add":
            if not item:
                return {"ok": False, "error": "nothing to add (empty item)"}
            items = _load(ctx)
            entry = {"text": item, "done": False, "ts": ctx.now("%Y-%m-%d %H:%M")}
            items.append(entry)
            _save(ctx, items)
            open_n = sum(1 for i in items if not i.get("done"))
            return {"ok": True, "data": {
                "action": "add", "added": item, "open": open_n}}

        if cmd == "list":
            items = _load(ctx)
            open_items = [i["text"] for i in items if not i.get("done")]
            return {"ok": True, "data": {
                "action": "list",
                "open": len(open_items),
                "items": open_items,
                "done_count": sum(1 for i in items if i.get("done"))}}

        if cmd in ("done", "complete", "finish", "remove", "delete"):
            items = _load(ctx)
            idx = _find(items, item)
            if idx is None:
                return {"ok": False,
                        "error": f"no matching item for {item!r}"}
            removed = items.pop(idx)
            _save(ctx, items)
            open_n = sum(1 for i in items if not i.get("done"))
            return {"ok": True, "data": {
                "action": "done", "completed": removed.get("text"),
                "open": open_n}}

        return {"ok": False, "error": f"unknown todo command: {cmd!r}"}

    def tools(ctx):
        return [{
            "name": "todo",
            "description": ("Maintain a local to-do list. command='add' (append "
                            "`item`), 'list' (open items), or 'done' (complete the "
                            "item matching `item`)."),
            "args": {
                "command": {"type": "enum", "required": True,
                            "values": ["add", "list", "done"]},
                "item": {"type": "string", "required": False},
            },
            "fn": do_todo,
        }]

    return {"tools": tools}
