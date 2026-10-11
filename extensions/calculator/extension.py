"""Calculator — reference skill extension.

Shows the two halves of a skill extension:
  1. a `tools` hook that DECLARES a callable skill for the watcher/registry;
  2. a plain pattern fallback (`on_transcript`) so it still works with no model.

Safe eval: only digits, operators, parentheses, and a few functions. Natural
phrasing (times / × / cubed / to the third power / divided by) is normalized
first, so spoken arithmetic does not fall through to the agent.
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

_ORD = {w: i for i, w in enumerate(
    ["zeroth", "first", "second", "third", "fourth", "fifth", "sixth",
     "seventh", "eighth", "ninth", "tenth"], 0)}


def normalize(expr):
    """Turn spoken arithmetic into a plain expression. Best-effort."""
    s = (expr or "").strip().lower().rstrip("?.!")
    s = s.replace("×", "*").replace("÷", "/").replace("−", "-")
    # powers: "to the third power", "to the power of 3", "cubed", "squared"
    s = re.sub(r"\bto the power of\s+(\d+)", r"^ \1", s)
    s = re.sub(r"\bto the\s+(\w+)\s+power", lambda m: "^ " + str(_ORD.get(m.group(1), "")), s)
    s = re.sub(r"\bto the power\s+(\d+)", r"^ \1", s)
    s = re.sub(r"\bsquared\b", "^ 2", s)
    s = re.sub(r"\bcubed\b", "^ 3", s)
    # word operators
    subs = [(r"\btimes\b", "*"), (r"\bmultiplied by\b", "*"),
            (r"\bplus\b", "+"), (r"\badd(?:ed to)?\b", "+"),
            (r"\bminus\b", "-"), (r"\btake away\b", "-"),
            (r"\bdivided by\b", "/"), (r"\bover\b", "/"),
            (r"\bmod(?:ulo)?\b", "%")]
    for pat, op in subs:
        s = re.sub(pat, op, s)
    # lone "x" between numbers -> *
    s = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", s)
    # tidy spacing around operators so "3 ^ 3" is valid, then ^ -> ** (Python pow)
    s = re.sub(r"\s*\^\s*", "^", s)
    s = s.replace("^", "**")
    return s.strip()


def safe_eval(expr):
    """Evaluate a pure arithmetic expression (after normalize). Raises otherwise."""
    expr = normalize(expr)
    if not expr or not EXPR_RE.match(expr):
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
            "description": ("Evaluate arithmetic. Accepts spoken forms: times, plus, "
                            "minus, divided by, squared, cubed, 'to the Nth power', "
                            "and the symbols x / ×/ ÷ / ^."),
            "args": {"expr": {"type": "string", "required": True}},
            "fn": do_calc,
            "speak": True,
        }]

    # -- no-model fallback: catch spoken arithmetic ------------------------
    spoken = re.compile(
        r"(?:what(?:'s| is)|whats|calculate|compute|how much is|"
        r"what(?:'s| is) the answer to)\s+(.+)", re.I)

    def on_transcript(text, ctx):
        m = spoken.search(text or "")
        if not m:
            return None
        try:
            val = safe_eval(m.group(1))
        except Exception:
            return None
        ctx.speak("That's " + _fmt(val) + ".")
        return ""

    return {"tools": tools, "on_transcript": on_transcript}
