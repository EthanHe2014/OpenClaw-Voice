"""Random — pure tool extension.

Exposes ONE tool: `random`. Rolls dice, flips a coin, or picks from a list, and
returns RESULT DATA. It does NOT speak and does NOT consume the turn — the
watcher routes to it, then OpenClaw phrases the spoken reply (TTS reads that).

The MODEL interprets language and fills `kind` (dice/coin/pick) and `spec`:
  - dice: `spec` is like "2d6" (count d sides) or just "d20"/"6" -> one die
  - coin: no spec
  - pick: `spec` is a comma-separated list of options
This tool stays dumb; it only parses a clean spec and draws.
"""
import random as _random


def _parse_dice(spec):
    """Parse a dice spec into (count, sides). Defaults to 1d6."""
    s = (spec or "").strip().lower().replace(" ", "")
    if not s:
        return 1, 6
    if "d" in s:
        left, _, right = s.partition("d")
        count = int(left) if left else 1
        sides = int(right) if right else 6
    else:
        count, sides = 1, int(s)
    count = max(1, min(100, count))
    sides = max(2, min(1000, sides))
    return count, sides


def setup(ctx):
    ctx.log("dice ready")

    def do_random(args, ctx):
        kind = (args.get("kind") or "dice").strip().lower()
        spec = args.get("spec") or ""

        if kind in ("dice", "die", "roll"):
            try:
                count, sides = _parse_dice(spec)
            except Exception:
                return {"ok": False, "error": f"bad dice spec {spec!r}"}
            rolls = [_random.randint(1, sides) for _ in range(count)]
            return {"ok": True, "data": {
                "action": "dice", "count": count, "sides": sides,
                "rolls": rolls, "total": sum(rolls)}}

        if kind in ("coin", "flip", "coinflip"):
            side = _random.choice(["heads", "tails"])
            return {"ok": True, "data": {"action": "coin", "result": side}}

        if kind in ("pick", "choose", "choice"):
            opts = [o.strip() for o in str(spec).split(",") if o.strip()]
            if len(opts) < 2:
                return {"ok": False,
                        "error": "pick needs at least two comma-separated options"}
            return {"ok": True, "data": {
                "action": "pick", "choice": _random.choice(opts),
                "from": opts}}

        return {"ok": False, "error": f"unknown random kind: {kind!r}"}

    def tools(ctx):
        return [{
            "name": "random",
            "description": ("Random draw. kind='dice' (spec like '2d6' or 'd20', "
                            "defaults to 1d6), 'coin' (flip heads/tails), or 'pick' "
                            "(spec is a comma-separated list of options)."),
            "args": {
                "kind": {"type": "enum", "required": True,
                         "values": ["dice", "coin", "pick"]},
                "spec": {"type": "string", "required": False},
            },
            "fn": do_random,
        }]

    return {"tools": tools}
