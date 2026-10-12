"""Spelling — pure tool extension.

Exposes ONE tool: `spell`. It spells a word letter-by-letter and/or looks up its
definition from the built-in macOS dictionary, then returns RESULT DATA. It does
NOT speak and does NOT consume the turn — the watcher routes to it, then OpenClaw
phrases the spoken reply (TTS reads that).

The MODEL interprets language and fills `command` (spell/define) and `word`.
This tool stays dumb: spelling is a pure string operation (always offline), and
definitions come from the system dictionary via DictionaryServices (also offline,
no credential, no network). Deterministic and fast. Only the `log` scope is used.
"""
import ctypes
from ctypes import Structure, c_char_p, c_long, c_uint32, c_void_p

_UTF8 = 0x08000100  # kCFStringEncodingUTF8

_CF = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_DS = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
       "DictionaryServices.framework/DictionaryServices")

_dict = None  # cached (cf, ds) handles, or False if unavailable


class _CFRange(Structure):
    _fields_ = [("location", c_long), ("length", c_long)]


def _load_dict():
    """Load CoreFoundation + DictionaryServices once. Never raises."""
    global _dict
    if _dict is not None:
        return _dict or None
    try:
        cf = ctypes.cdll.LoadLibrary(_CF)
        ds = ctypes.cdll.LoadLibrary(_DS)
        cf.CFStringCreateWithCString.restype = c_void_p
        cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
        cf.CFStringGetLength.restype = c_long
        cf.CFStringGetLength.argtypes = [c_void_p]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_long, c_uint32]
        cf.CFRelease.argtypes = [c_void_p]
        ds.DCSCopyTextDefinition.restype = c_void_p
        ds.DCSCopyTextDefinition.argtypes = [c_void_p, c_void_p, _CFRange]
        _dict = (cf, ds)
    except Exception:
        _dict = False
    return _dict or None


def _define(word):
    """Return (ok, definition-or-error) using the system dictionary."""
    libs = _load_dict()
    if not libs:
        return False, "system dictionary unavailable"
    cf, ds = libs
    try:
        cw = cf.CFStringCreateWithCString(None, word.encode("utf-8"), _UTF8)
        if not cw:
            return False, "couldn't encode word"
        try:
            n = cf.CFStringGetLength(cw)
            out = ds.DCSCopyTextDefinition(None, cw, _CFRange(0, n))
        finally:
            cf.CFRelease(cw)
        if not out:
            return False, "no definition found"
        try:
            buf = ctypes.create_string_buffer(8192)
            if not cf.CFStringGetCString(out, buf, 8192, _UTF8):
                return False, "definition too long to read"
            text = buf.value.decode("utf-8", "replace")
        finally:
            cf.CFRelease(out)
        return True, text
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _letters(word):
    """Split a word into the spoken letters, keeping spaces/hyphens sensible."""
    out = []
    for ch in word.strip():
        if ch == " ":
            out.append("(space)")
        elif ch == "-":
            out.append("(hyphen)")
        elif ch.isalpha():
            out.append(ch.lower())
        else:
            out.append(ch)
    return out


def setup(ctx):
    ctx.log("spelling ready")

    def do_spell(args, ctx):
        cmd = (args.get("command") or "spell").strip().lower()
        word = (args.get("word") or "").strip()

        if not word:
            return {"ok": False, "error": "no word given to spell"}

        if cmd in ("spell", "spelling", "letters"):
            letters = _letters(word)
            return {"ok": True, "data": {
                "action": "spell", "word": word,
                "length": len(word.replace(" ", "").replace("-", "")),
                "letters": letters,
                "spelled": " - ".join(letters)}}

        if cmd in ("define", "definition", "meaning", "what"):
            ok, res = _define(word)
            if not ok:
                return {"ok": True, "data": {
                    "action": "define", "word": word,
                    "found": False, "note": res}}
            return {"ok": True, "data": {
                "action": "define", "word": word,
                "found": True, "definition": res}}

        return {"ok": False, "error": f"unknown spell command: {cmd!r}"}

    def tools(ctx):
        return [{
            "name": "spell",
            "description": ("Spell a word or look up its definition. command='spell' "
                            "(list the letters of `word`) or 'define' (its meaning "
                            "from the macOS dictionary)."),
            "args": {
                "command": {"type": "enum", "required": True,
                            "values": ["spell", "define"]},
                "word": {"type": "string", "required": True},
            },
            "fn": do_spell,
        }]

    return {"tools": tools}
