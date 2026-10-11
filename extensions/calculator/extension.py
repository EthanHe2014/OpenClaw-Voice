"""Calculator — pure tool extension.

Exposes ONE tool: `calculator`. Returns the computed value as DATA. It does NOT
speak and does NOT consume the turn — OpenClaw phrases the reply.

The MODEL does the interpreting: it emits a clean `expr` (e.g. "100*3**3") and
this tool only evaluates it. Keep the evaluator dumb and safe.
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

EXPR_RE = re.compile(r"^[\d\s\.\+\-\*/%()]+$")


def safe_eval(expr):
    """Evaluate a pure arithmetic expression. Raises on anything else."""
    s = (expr or "").strip()
    s = s.replace("^", "**")          # caret = power (common convention)
    if not s or not EXPR_RE.match(s):
        raise ValueError("not a pure arithmetic expression")
    node = ast.parse(s, mode="eval")
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
    return v


def setup(ctx):
    ctx.log("calculator ready")

    def do_calc(args, ctx):
        expr = args.get("expr", "")
        try:
            val = safe_eval(expr)
        except Exception as e:
            return {"ok": False, "error": f"bad expr {expr!r}: {e}"}
        return {"ok": True, "data": {"expr": expr, "value": _fmt(val)}}

    def tools(ctx):
        return [{
            "name": "calculator",
            "description": ("Evaluate an arithmetic expression. `expr` must be a "
                            "plain expression using only digits and + - * / ( ) "
                            "and ** for powers (e.g. \"100*3**3\")."),
            "args": {"expr": {"type": "string", "required": True}},
            "fn": do_calc,
        }]

    return {"tools": tools}
