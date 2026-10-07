#!/usr/bin/env python3
"""
Spark voice reply-bridge.

Watches voice_inbox.jsonl for utterances the listener captured, asks the Spark
agent for an answer, and writes the reply to voice_outbox/<id>.txt so the
listener can speak it.

Two modes:
  --serve     run as a loop (used by the LaunchAgent)
  --once      process whatever is currently queued and exit (for testing)

The actual agent call is pluggable via SPARK_AGENT_CMD — a shell command that
takes the user text on stdin and prints the reply on stdout. If unset, uses a
built-in fallback so the loop is testable without the agent.
"""
import os, sys, json, time, subprocess, uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(ROOT, "voice_inbox.jsonl")
OUTBOX = os.path.join(ROOT, "voice_outbox")
PROCESSED = os.path.join(ROOT, "voice_processed.txt")
# Default brain: one-shot model turn as the Spark agent via the OpenClaw CLI.
# Read the prompt on stdin, print the reply on stdout. Override with SPARK_AGENT_CMD.
OPENCLAW_CLI = os.environ.get("SPARK_OPENCLAW_CLI", "openclaw")
AGENT_ID = os.environ.get("SPARK_AGENT_ID", "spark")

def _default_agent_cmd(text):
    """Run openclaw infer model run --agent spark --prompt <text>; return reply."""
    cmd = [
        OPENCLAW_CLI, "infer", "model", "run",
        "--agent", AGENT_ID, "--prompt", _VOICE_SYSTEM + text,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    out = (p.stdout or "").strip()
    # The CLI prints banners + a "outputs: 1" block; take the text AFTER the outputs line.
    if "outputs:" in out:
        tail = out.split("outputs:", 1)[1]
        # drop the leading count token on that line
        lines = tail.splitlines()
        if lines and lines[0].strip().isdigit():
            out = "\n".join(lines[1:]).strip()
        else:
            out = tail.strip()
    # Strip leftover box-drawing / warning banners
    clean = []
    for ln in out.splitlines():
        s = ln.strip()
        if not s: continue
        if s[0] in "│├╭╰─┌└╮╯┐┘": continue
        if s.startswith("model.run") or s.startswith("provider:") or s.startswith("model:"):
            continue
        if "WARNING:" in s or "Reconciled" in s or "Backup:" in s: continue
        clean.append(s)
    return "\n".join(clean).strip()

# Spoken-style system prelude so answers are short and speakable.
_VOICE_SYSTEM = ("You are Spark, a friendly voice assistant. Answer in ONE short, "
                 "spoken-style sentence. No markdown, no lists, no emoji. "
                 "User said: ")

AGENT_CMD = os.environ.get("SPARK_AGENT_CMD", "")

os.makedirs(OUTBOX, exist_ok=True)

def log(*a):
    print("[bridge]", *a, flush=True)

def already_processed(rid):
    if not os.path.exists(PROCESSED):
        return False
    with open(PROCESSED) as f:
        return rid in f.read().splitlines()

def mark_processed(rid):
    with open(PROCESSED, "a") as f:
        f.write(rid + "\n")

def get_reply(text):
    """Route the utterance to the Spark agent. Returns reply text."""
    if not AGENT_CMD:
        try:
            return _default_agent_cmd(text)
        except Exception as e:
            log("default agent cmd failed:", e)
    if AGENT_CMD:
        try:
            p = subprocess.run(AGENT_CMD, shell=True, input=text,
                               capture_output=True, text=True, timeout=60)
            out = (p.stdout or "").strip()
            if out:
                return out
        except Exception as e:
            log("agent cmd failed:", e)
    # Fallback so the loop is testable end-to-end without the agent.
    return f"I heard you say: {text}"

def process_pending():
    if not os.path.exists(INBOX):
        return 0
    n = 0
    with open(INBOX) as f:
        lines = f.read().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid = req.get("id") or str(uuid.uuid4())
        if already_processed(rid):
            continue
        text = str(req.get("text", "")).strip()
        if not text:
            mark_processed(rid); continue
        reply = get_reply(text)
        with open(os.path.join(OUTBOX, rid + ".txt"), "w") as f:
            f.write(reply)
        mark_processed(rid)
        log(f"{rid} :: {text!r} -> {reply!r}")
        n += 1
    # Trim processed inbox to avoid unbounded growth
    try:
        with open(INBOX, "w") as f:
            f.write("")
    except Exception:
        pass
    return n

def main():
    if "--once" in sys.argv:
        n = process_pending()
        log(f"processed {n}")
        return
    log("watching", INBOX)
    while True:
        try:
            process_pending()
        except Exception as e:
            log("err:", e)
        time.sleep(0.5)

if __name__ == "__main__":
    main()
