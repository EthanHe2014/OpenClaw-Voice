"""Clock & Time — reference extension.

Teaches Spark to answer the time/date on the spot, and shows how an
`on_transcript` hook pre-empts the agent: it replies itself, then returns the
text unchanged so nothing else breaks.
"""
import re

# A handful of phrasings people actually use. Lowercased matching.
TIME_RE = re.compile(r"\b(what(?:'s| is)? the time|what time is it|"
                     r"time is it|got the time|current time)\b")
DATE_RE = re.compile(r"\b(what(?:'s| is)? (?:the )?date|what day is it|"
                     r"what(?:'s| is)? today(?:'s date)?)\b")


def setup(ctx):
    ctx.log("clock extension ready")

    def on_transcript(text, ctx):
        t = (text or "").lower().strip()
        if not t:
            return None
        if TIME_RE.search(t):
            ctx.speak("It is " + ctx.now("%H:%M") + " right now.")
            return ""          # consumed: hand the agent nothing to do
        if DATE_RE.search(t):
            ctx.speak("Today is " + ctx.now("%A, %B %-d, %Y") + ".")
            return ""
        return None            # not ours — pass the text through

    return {"on_transcript": on_transcript}
