"""Calculator — reference skill extension.

Shows the two halves of a skill extension:
  1. a `tools` hook that DECLARES a callable skill for the watcher/registry;
  2. a plain pattern fallback (`on_transcript`) so it still works with no model.

Safe eval: only digits, operators, parentheses, and a few functions.
"""
import ast
import operator
import re

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

EXPR_RE = re.compile(r"^[\d\s\.\+\-\*/%\(\)]+$")


def safe_eval(expr):
    """Evaluate a pure arithmetic expression. Raises on anything else."""
    expr = (expr or "").strip().replace("x", "*").replace("×", "*").replace("÷", "/")
    if not EXPR_RE.match(expr):
        raise ValueError("not a pure arithmetic expression")
    node = ast.parse(expr, mode="eval")
    return _eval(node.body)


def _eval(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_eval(n.left), _eval(n.right))
    if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_eval(n.operand))
    raise ValueError("unsupported expression")


def _fmt(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def setup(ctx):
    ctx.log("calculator ready")

    # -- skill the watcher can route to ------------------------------------
    def do_calc(args, ctx):
        val = safe_eval(args.get("expr", ""))
        return {"ok": True, "speak": _fmt(val), "data": {"value": val}}

    def tools(ctx):
        return [{
            "name": "calculator",
            "description": "Evaluate an arithmetic expression (+, -, *, /, %, (, ), x/×).",
            "args": {"expr": {"type": "string", "required": True}},
            "fn": do_calc,
            "speak": True,
        }]

    # -- no-model fallback: catch obvious "what's 47 times 89" --------------
    spoken = re.compile(r"(?:what(?:'s| is)|calculate|compute|how much is)\s+(.+)", re.I)
    words = {"times": "*", "plus": "+", "minus": "-", "divided by": "/",
             "over": "/", "x": "*"}

    def on_transcript(text, ctx):
        m = spoken.search(text or "")
        if not m:
            return None
        expr = m.group(1).strip().rstrip("?.!")
        for w, op in words.items():
            expr = re.sub(rf"\b{w}\b", op, expr, flags=re.I)
        try:
            val = safe_eval(expr)
        except Exception:
            return None
        ctx.speak("That's " + _fmt(val) + ".")
        return ""

    return {"tools": tools, "on_transcript": on_transcript}
