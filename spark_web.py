#!/usr/bin/env python3
"""
Spark web dashboard — live voice-pipeline monitor served over HTTP.

Runs its own mic loop (same wakeword pipeline), keeps state in memory, and
serves a live dashboard at http://127.0.0.1:8770 with a /state JSON endpoint
the page polls.

Run: ./.venv/bin/python spark_web.py
"""
import os, sys, json, time, threading, subprocess, tempfile, wave, re, urllib.parse, sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import numpy as np

# --- configuration: spark_config.json + SPARK_* env, with safe fallbacks ---
try:
    from spark_config import CFG
except Exception:
    CFG = None
ROOT = os.path.dirname(os.path.abspath(__file__))

def _cfg(key, default):
    return getattr(CFG, key, default) if CFG is not None else default

_proxy = _cfg("proxy", "")
if _proxy:
    os.environ.setdefault("https_proxy", _proxy)
    os.environ.setdefault("http_proxy", _proxy)
    os.environ.setdefault("all_proxy", _proxy)

import pyaudio
from openwakeword.model import Model

MIC_SUBSTR = _cfg("mic_name_substr", "USB PnP")
FALLBACK = _cfg("mic_index_fallback", 2)
FORCE_MIC_INDEX = _cfg("force_mic_index", None)
NATIVE = _cfg("native_rate", 48000); RATE = _cfg("rate", 16000); FRAME_MS = _cfg("frame_ms", 80)
WAKE = _cfg("wake_model", os.path.join(ROOT, "wake_models", "hey_spark.onnx")); THRESH = _cfg("wake_threshold", 0.3)
PORT = _cfg("port", 8770)
SILENCE_MS = _cfg("silence_ms", 3000); MAX_UTTER_S = _cfg("max_utter_s", 20.0)  # word-based close
CONFIRM_WORD = _cfg("confirm_word", "")
WHISPER_BIN = _cfg("whisper_bin", "whisper-cli")
WHISPER_MODEL = _cfg("whisper_model", os.path.join(ROOT, "models", "ggml-base.bin"))
MACOS_SR_BIN = _cfg("sr_bin", os.path.join(ROOT, "sr_test"))
STT_ENGINE = _cfg("stt_engine", "macos")     # "macos" | "whisper"
STT_LANG = _cfg("stt_language", "en")
TTS_VOICE = _cfg("voice", "en-US-AvaNeural")
TTS_FALLBACK = _cfg("tts_fallback", "say")


def transcribe_macos(wav_path):
    """Transcribe with the Mac's own Speech framework (accurate for usage here).
    Returns the text (possibly "" for silence/no-speech), or None if the tool
    itself failed to run — so the caller can tell 'no speech' from 'unavailable'."""
    try:
        r = subprocess.run([MACOS_SR_BIN, wav_path], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            add_event("ERROR", f"macos sr rc={r.returncode}: {(r.stderr or '').strip()[:80]}")
            return None
        return (r.stdout or "").strip()
    except Exception as e:
        add_event("ERROR", f"macos sr: {e}")
        return None


def transcribe_audio(wav_path):
    """Dispatch to the configured STT engine, with graceful fallback.
    In macos mode an EMPTY result means 'no speech' and is returned as-is;
    we only fall back to whisper when the Apple recognizer could not run."""
    if STT_ENGINE == "whisper":
        return transcribe(wav_path)
    txt = transcribe_macos(wav_path)
    if txt is None:                  # Apple Speech unavailable -> try whisper
        txt = transcribe(wav_path)
    return txt or ""
AGENT_ID = _cfg("agent_id", "spark")
REPLY_FILE = _cfg("reply_file", os.path.join(ROOT, "voice_reply.txt"))
HOOK_URL = _cfg("hook_url", "http://127.0.0.1:18789/hooks/voice/agent")
GATEWAY_API = _cfg("gateway_url", "http://127.0.0.1:18789")
VOICE_SESSION = _cfg("session_key", "")
HOOK_TOKEN_FILE = _cfg("hook_token_file", os.path.join(ROOT, ".hook_token"))
_last_run_id = None

STATE = {
    "mic_index": None, "mic_name": "", "mic_pinned": None, "frames": 0, "started": time.time(),
    "rms": 0.0, "rms_peak": 0.0, "score": 0.0, "score_peak": 0.0,
    "events": [], "last_wake": None,
    "last_transcript": "", "last_reply": "", "state": "idle",
}
LOCK = threading.Lock()

# Extension framework + watcher router (populated by init_extensions() at startup).
# Both stay None if the framework fails to load - the pipeline then behaves
# exactly as before, which is the safety invariant.
EXT_MGR = None
BRIDGE = None
EXT_REG = None

def add_event(kind, detail):
    with LOCK:
        STATE["events"].append({"t": time.strftime("%H:%M:%S"), "kind": kind, "detail": detail})
        STATE["events"] = STATE["events"][-25:]
        if kind == "WAKE":
            STATE["last_wake"] = STATE["events"][-1]["t"]

TURNS_FILE = os.path.join(ROOT, "voice_turns.jsonl")

def log_turn(text):
    """Append each user utterance to a turn log the task engine's monitor reads
    (L1/L2 landing checks). Timestamped so the engine can match it to created tasks."""
    if not text:
        return
    try:
        with open(TURNS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(),
                                "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "text": text}, ensure_ascii=False) + "\n")
    except Exception as e:
        add_event("ERROR", f"turn log: {e}")

# Speech recognizers tag non-speech with brackets: "(bell ding)", "(music)", "[noise]".
# A transcript that is ONLY such tag(s) is noise, not the user talking — drop it
# before logging or sending, so it never costs credits.
_NOISE_ONLY_RE = re.compile(r'^\s*(?:[\(\[\{][^\)\]\}]*[\)\]\}]\s*)+[.!?,;:\s]*$')

def is_noise_only(text):
    return bool(text) and bool(_NOISE_ONLY_RE.match(text))

def _valid_input(pa, i):
    try:
        d = pa.get_device_info_by_index(i)
        return d.get("maxInputChannels",0) > 0, d.get("name","")
    except Exception:
        return False, ""

def input_candidates(pa):
    """Capturable input device indexes, best-first: explicit override, then the
    configured name match, then ANY other input device, then the fallback."""
    cands = []
    if FORCE_MIC_INDEX is not None:
        ok, _ = _valid_input(pa, FORCE_MIC_INDEX)
        if ok:
            cands.append(FORCE_MIC_INDEX)
    sub = (MIC_SUBSTR or "").strip().lower()
    if sub:
        for i in range(pa.get_device_count()):
            if i in cands:
                continue
            d = pa.get_device_info_by_index(i)
            if d.get("maxInputChannels", 0) > 0 and sub in d.get("name", "").lower():
                cands.append(i)
    # ANY device that can actually capture audio (no name filtering)
    for i in range(pa.get_device_count()):
        if i in cands:
            continue
        d = pa.get_device_info_by_index(i)
        if d.get("maxInputChannels", 0) > 0:
            cands.append(i)
    if FALLBACK not in cands:
        cands.append(FALLBACK)
    return cands

def find_mic(pa):
    """First usable input device, best-first."""
    for i in input_candidates(pa):
        ok, nm = _valid_input(pa, i)
        if ok:
            return i, nm
    return FALLBACK, "(fallback)"

def resample(a, src):
    if src == RATE: return a
    n = int(len(a)*RATE/src)
    return np.interp(np.linspace(0, len(a)-1, n), np.arange(len(a)), a).astype(np.int16)

def _resample(a, src):
    return resample(a, src)

def transcribe(path):
    try:
        r = subprocess.run([WHISPER_BIN, "-m", WHISPER_MODEL, "-f", path,
                            "-l", STT_LANG, "-nt", "-np"],
                           capture_output=True, text=True, timeout=20)
        return (r.stdout or "").strip()
    except Exception as e:
        add_event("ERROR", f"whisper: {e}"); return ""

RISKY_PATTERNS = [
    r"\bdelete\b", r"\bremove\b", r"\brm\b", r"\btrash\b", r"\berase\b", r"\bwipe\b",
    r"\bformat\b", r"\binstall\b", r"\buninstall\b", r"\bsudo\b", r"\bchmod\b",
    r"\bkill\b", r"\bpkill\b", r"\bshutdown\b", r"\breboot\b", r"\bkillall\b",
    r"\bmove\b", r"\boverwrite\b", r"\bcurl\b.*\|", r"\bdownload\b", r"\bopen\b.*\bapp\b",
    r"/etc/", r"/System/", r"/Library/", r"\.ssh\b", r"\bpassword", r"\bkeychain\b",
]

def is_risky(text):
    t = (text or "").lower()
    return any(re.search(p, t) for p in RISKY_PATTERNS)


def _fuzzy_find(name, base):
    """Find a file under base matching name (case-insensitive, extension-optional).
    Tolerates a trailing plural 's' (STT often hears 'avatars.jpg' for 'Avatar.jpg')."""
    if not base or not os.path.isdir(base):
        return None
    target = name.lower().strip()
    stem = os.path.splitext(target)[0]
    cands = os.listdir(base)

    def _try(st, ext):
        # exact full name
        for c in cands:
            if c.lower() == (st + ext):
                return os.path.join(base, c)
        # stem match
        for c in cands:
            if os.path.splitext(c.lower())[0] == st:
                return os.path.join(base, c)
        # substring
        for c in cands:
            if st and st in c.lower():
                return os.path.join(base, c)
        return None

    ext = os.path.splitext(target)[1]
    hit = _try(stem, ext)
    if hit:
        return hit
    # plural/singular fallbacks: drop or add a trailing 's' on the stem
    if stem.endswith("s"):
        hit = _try(stem[:-1], ext)
        if hit:
            return hit
    else:
        hit = _try(stem + "s", ext)
        if hit:
            return hit
    return None

ALLOWED_DIRS = [os.path.join(os.path.expanduser("~"), d)
                for d in ("Desktop", "Documents", "Downloads", "Projects")] + [ROOT]

APP_ALIASES = {
    "notes": "Notes", "safari": "Safari", "chrome": "Google Chrome",
    "music": "Music", "spotify": "Spotify", "finder": "Finder",
    "calculator": "Calculator", "calendar": "Calendar", "mail": "Mail",
    "photos": "Photos", "terminal": "Terminal", "settings": "System Settings",
}

def _load_mail_account():
    """Read the 163 SEND settings from himalaya's config.
    Note: the file has both IMAP (backend.*) and SMTP (message.send.backend.*)
    host/port pairs — we must take the SMTP one for sending."""
    cfg = os.path.expanduser("~/.config/himalaya/config.toml")
    txt = open(cfg).read()
    email = re.search(r'email\s*=\s*"([^"]+)"', txt)
    user = re.search(r'(?:login|user)\s*=\s*"([^"]+)"', txt) or email
    auth = re.search(r'raw\s*=\s*"([^"]+)"', txt)
    # Prefer the send-backend host/port; fall back to defaults.
    sh = re.search(r'message\.send\.backend\.host\s*=\s*"([^"]+)"', txt)
    sp = re.search(r'message\.send\.backend\.port\s*=\s*(\d+)', txt)
    return {
        "email": email.group(1) if email else "",
        "user": user.group(1) if user else "",
        "auth": auth.group(1) if auth else "",
        "host": sh.group(1) if sh else "smtp.163.com",
        "port": int(sp.group(1)) if sp else 465,
    }

def _smtp_send(to_addr, subject, body):
    """Send via SMTP directly. Confirm by the 250 response, never by exit code.
    Returns (ok, detail)."""
    import smtplib, email.message
    a = _load_mail_account()
    if not (a["user"] and a["auth"]):
        return False, "no mail credentials found"
    msg = email.message.EmailMessage()
    msg["From"] = a["email"] or a["user"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(a["host"], a["port"], timeout=25) as s:
            s.login(a["user"], a["auth"])
            refused = s.send_message(msg)
        if refused:
            return False, "refused: " + str(refused)
        return True, "250 accepted"   # no refused recipients == accepted
    except Exception as e:
        return False, str(e)

def ask_spark(text):
    """Send the utterance to the REAL Spark agent via the local hook, then read
    the reply from the persistent session. Returns reply text."""
    import urllib.request
    global _last_run_id
    try:
        tok = open(HOOK_TOKEN_FILE).read().strip()
    except Exception as e:
        add_event("ERROR", f"hook token: {e}"); return ""
    # Voice arrives as Ethan. Body is ONLY the user's words plus a brief-mode note.
    # The Gateway adds its own untrusted/job framing around this; we cannot remove
    # that from here, so we keep our own contribution minimal.
    framed = (text +
              "\n\n--- VOICE MODE ---"
              "\nTone: be cheerful, warm, lively and a little playful. Friendly energy, light and human, like talking to a friend."
              "\nKeep the spoken reply short: 2-4 natural sentences."
              "\nAfter you speak, write that exact reply to the file ./voice_reply.txt using the write tool,"
              " as your final action of the turn, so the local speaker can read it out. Plain text, no markdown, no emoji."
              "\n\n--- SILENT REMINDERS ---"
              "\nIf the user implies a time-bound plan (even without asking for a reminder), silently add it by writing ONE json file"
              " into /Users/Ethan/Desktop/Projects/Spark Voice/voice_inbox/ (any name ending in .json) with these keys:"
              " {\"content\": short label, \"time\": \"YYYY-MM-DD HH:MM\" (absolute local, 24h), \"speak\": the EXACT English words to say aloud, \"recurring\": null, \"priority\": \"medium\"}."
              " Do NOT mention this reminder out loud — it must feel seamless/discreet."
              " Only if the user EXPLICITLY asked for a reminder, confirm it in your spoken reply."
              " speak must be English. Full contract: TASKS_INBOX.md.")
    body = {
        "message": framed, "agentId": AGENT_ID,
        "sessionMode": "persistent", "waitForCompletion": True,
    }
    if VOICE_SESSION:
        body["sessionKey"] = VOICE_SESSION
    body = json.dumps(body).encode()
    # Snapshot the reply file's mtime BEFORE sending, so any newer write is ours.
    try:
        before_mtime = os.path.getmtime(REPLY_FILE)
    except OSError:
        before_mtime = 0.0
    req_ts_ms = time.time() * 1000.0
    try:
        open(REPLY_FILE, "w").close()
    except Exception:
        pass
    req = urllib.request.Request(
        HOOK_URL, data=body,
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            _HOOK["resp"] = r
            try:
                resp = json.loads(r.read().decode())
            finally:
                _HOOK["resp"] = None
    except Exception as e:
        if _INTERRUPT.is_set():
            return ""
        add_event("ERROR", f"hook: {e}"); return ""
    if _INTERRUPT.is_set():
        return ""
    if not resp.get("ok"):
        add_event("ERROR", f"hook not ok: {resp.get('error','?')}"); return ""
    _last_run_id = resp.get("runId")
    # Read the newest assistant reply written AFTER we sent the request.
    return _read_last_reply(timeout=40, before_mtime=before_mtime)

def _read_last_reply(timeout=40, before_mtime=0.0):
    """Return the agent's reply once voice_reply.txt is written afresh.
    before_mtime is the file mtime captured before the request was sent."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _INTERRUPT.is_set():
            return ""
        try:
            if os.path.exists(REPLY_FILE):
                if os.path.getmtime(REPLY_FILE) > before_mtime:
                    with open(REPLY_FILE) as f:
                        txt = f.read().strip()
                    if txt:
                        return txt
        except Exception as e:
            add_event("ERROR", f"read reply: {e}")
        time.sleep(0.05)
    return ""

_PHRASE_CACHE = {}

def _synth(text, voice, outp):
    try:
        import asyncio, edge_tts
        async def go():
            await edge_tts.Communicate(text, voice).save(outp)
        done = {"ok": False}
        def run():
            try:
                asyncio.run(go()); done["ok"] = True
            except Exception as e:
                add_event("ERROR", f"tts: {e}")
        t = threading.Thread(target=run); t.start(); t.join(timeout=10)
        return done["ok"] and os.path.exists(outp) and os.path.getsize(outp) > 0
    except Exception as e:
        add_event("ERROR", f"tts: {e}")
        return False

def prewarm():
    """Pre-synthesize the acknowledgement so it plays instantly."""
    # New soft-hum cues (files in spark_cues/)
    for key, fn in (("go_on", "wake.wav"), ("thinking", "thinking.wav"), ("missed", "error.wav"), ("done", "done.wav")):
        p = os.path.join(ROOT, "spark_cues", fn)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            _PHRASE_CACHE[key] = p

def speak(text):
    if not text: return
    # Instant path: a pre-synthesized phrase.
    if text.strip().lower() in ("go on", "yes?") and _PHRASE_CACHE.get("go_on"):
        _play_file(_PHRASE_CACHE["go_on"])
        return
    voice = TTS_VOICE
    outp = tempfile.mktemp(suffix=".wav")
    try:
        import asyncio, edge_tts
        async def go():
            await edge_tts.Communicate(text, voice).save(outp)
        done = {"ok": False}
        def run():
            try: asyncio.run(go()); done["ok"] = True
            except Exception as e: add_event("ERROR", f"tts: {e}")
        t = threading.Thread(target=run); t.start()
        while t.is_alive():
            if _INTERRUPT.is_set():
                return              # Stop pressed: never speak this reply
            t.join(timeout=0.05)
        if done["ok"] and os.path.exists(outp) and os.path.getsize(outp) > 0:
            _play_file(outp)
            return
    except Exception as e:
        add_event("ERROR", f"tts: {e}")
    finally:
        try:
            if os.path.exists(outp): os.remove(outp)
        except Exception: pass
    if not _INTERRUPT.is_set():
        try:
            p = subprocess.Popen(["/usr/bin/say", text])
            _play["proc"] = p
            p.wait()
        finally:
            _play["proc"] = None

_think_stop = {"flag": False, "proc": None}
_play = {"proc": None}                 # current afplay process (TTS or cue)
_INTERRUPT = threading.Event()        # set by the Stop button to abandon the turn
_HOOK = {"resp": None}               # in-flight hook response, so Stop can drop it


def _play_file(path):
    """Play a wav, but abort instantly if Stop is pressed. True = played fully."""
    try:
        p = subprocess.Popen(["/usr/bin/afplay", path])
    except Exception as e:
        add_event("ERROR", f"play: {e}"); return False
    _play["proc"] = p
    try:
        p.wait()
    finally:
        _play["proc"] = None
    return not _INTERRUPT.is_set()


def start_thinking_loop():
    """Loop the thinking hum evenly until stop_thinking_loop() is called."""
    p = _PHRASE_CACHE.get("thinking")
    if not p or not os.path.exists(p):
        return
    _think_stop["flag"] = False
    def _run():
        while not _think_stop["flag"]:
            try:
                proc = subprocess.Popen(["/usr/bin/afplay", p])
                _think_stop["proc"] = proc
                proc.wait()
                if _think_stop["flag"]:
                    break
                time.sleep(0.12)   # even gap between hums
            except Exception:
                break
    threading.Thread(target=_run, daemon=True).start()


def stop_thinking_loop(force=False):
    """Stop the hum. force=True kills the current tone at once (Stop button);
    otherwise let the current hum finish so the rhythm never breaks mid-tone."""
    _think_stop["flag"] = True
    proc = _think_stop["proc"]
    if proc is not None:
        if force:
            try: proc.kill()
            except Exception: pass
        else:
            try:
                proc.wait(timeout=3)   # let the current hum complete
            except Exception:
                try: proc.kill()
                except Exception: pass


def do_stop():
    """The Stop button: end thinking, drop the in-flight webhook, kill TTS,
    and return the pipeline to idle."""
    _INTERRUPT.set()
    stop_thinking_loop(force=True)          # kill the hum now
    p = _play.get("proc")
    if p is not None:
        try: p.kill()                        # kill any TTS/cue audio now
        except Exception: pass
    r = _HOOK.get("resp")
    if r is not None:
        try: r.close()                       # drop the webhook wait
        except Exception: pass
    with LOCK: STATE["state"] = "idle"
    add_event("STOP", "stopped by button")


def play_cached(key):
    """Play a pre-synthesized cue file instantly (no TTS call)."""
    p = _PHRASE_CACHE.get(key)
    if not p or not os.path.exists(p):
        return
    try:
        _play_file(p)
    except Exception as e:
        add_event("ERROR", f"cue {key}: {e}")


def speak_ack():
    """Short spoken cue right after the wake word so Ethan knows it is listening."""
    try:
        speak("Yes?")
    except Exception as e:
        add_event("ERROR", f"ack: {e}")

def dedupe_phrase(text):
    """whisper sometimes repeats a phrase; collapse exact repeats."""
    t = (text or "").strip()
    if not t:
        return t
    # Try to split into sentences/segments and drop consecutive identical ones.
    import re as _re
    parts = [p.strip() for p in _re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    if len(parts) > 1:
        uniq = []
        for p in parts:
            if not uniq or uniq[-1] != p:
                uniq.append(p)
        if len(uniq) < len(parts):
            return " ".join(uniq)
    # Fallback: collapse an exact phrase repeated 2+ times in a row.
    for n in range(len(t)//2, 3, -1):
        chunk = t[:n].strip()
        if chunk and t == (chunk + " " + chunk):
            return chunk
    return t

class MicBuffer:
    """Continuously reads the mic in a background thread into a deque.
    Lets capture() consume from the buffer so audio spoken during the ack
    (while playback blocks) is not lost."""
    def __init__(self, stream, CH, maxlen=400):
        import collections
        self.stream = stream; self.CH = CH
        self.buf = collections.deque(maxlen=maxlen)
        self.stop = False
        self.t = None
    def start(self):
        def _run():
            while not self.stop:
                try:
                    raw = self.stream.read(self.CH, exception_on_overflow=False)
                    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                    self.buf.append(resample(a, NATIVE))
                except Exception:
                    time.sleep(0.01)
        self.t = threading.Thread(target=_run, daemon=True); self.t.start()
        return self
    def drain(self):
        """Take everything currently buffered (start of the utterance)."""
        out = list(self.buf); self.buf.clear(); return out
    def halt(self):
        self.stop = True
        if self.t: self.t.join(timeout=0.15)

def _pcm_to_wav_bytes(arr):
    import io as _io
    b = _io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(arr.astype(np.int16).tobytes())
    return b.getvalue()

def _whisper_words(arr):
    """Return the word count whisper hears in this audio slice (0 on error)."""
    if len(arr) < RATE * 0.25:
        return 0
    p = tempfile.mktemp(suffix=".wav")
    try:
        with wave.open(p, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
            w.writeframes(arr.astype(np.int16).tobytes())
        txt = transcribe(p)
        return len([x for x in re.findall(r"[A-Za-z']+", txt or "")])
    except Exception:
        return 0
    finally:
        try: os.remove(p)
        except Exception: pass

# --- Streaming sherpa-onnx endpointing (sherpa-onnx) ---
SR_MODEL_DIR = _cfg("sr_model_dir", "")   # optional sherpa-onnx streaming model dir
_sr = None


def _get_sr():
    """Streaming recognizer with built-in endpoint detection (optional)."""
    global _sr
    if _sr is not None:
        return _sr
    if not SR_MODEL_DIR or not os.path.isdir(SR_MODEL_DIR):
        return None
    try:
        import sherpa_onnx
        _sr = sherpa_onnx.OnlineRecognizer.from_transducer(
            encoder=os.path.join(SR_MODEL_DIR, "encoder-epoch-99-avg-1.onnx"),
            decoder=os.path.join(SR_MODEL_DIR, "decoder-epoch-99-avg-1.onnx"),
            joiner=os.path.join(SR_MODEL_DIR, "joiner-epoch-99-avg-1.onnx"),
            tokens=os.path.join(SR_MODEL_DIR, "tokens.txt"),
            num_threads=2, sample_rate=RATE, feature_dim=80,
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=3.0,
            rule2_min_trailing_silence=1.6,
            rule3_min_utterance_length=0.3,
        )
    except Exception as e:
        add_event("ERROR", f"sherpa load: {e}")
        _sr = None
    return _sr


def capture_from(buf, timeout_frames=None, preroll=None):
    """Capture using a streaming-recognizer approach: a streaming recognizer with endpoint
    detection decides when the utterance ends. No RMS gate, no fixed timers."""
    rec = _get_sr()
    frames = []
    if rec is None:
        # no recognizer: fall back to a short fixed capture
        if preroll: frames.extend(preroll)
        t0 = time.time()
        while time.time() - t0 < 6.0:
            while buf.buf: frames.append(buf.buf.popleft())
            time.sleep(0.01)
        return np.concatenate(frames) if frames else np.array([], dtype=np.int16)

    stream = rec.create_stream()
    heard_text = ""
    last_new_word_t = None   # set when the recognised text GROWS
    t0 = time.time()
    while time.time() - t0 < MAX_UTTER_S:
        got = False
        while buf.buf:
            a16 = buf.buf.popleft()
            frames.append(a16)
            got = True
            stream.accept_waveform(RATE, a16.astype(np.float32) / 32768.0)
            while rec.is_ready(stream):
                rec.decode_stream(stream)
        txt = (rec.get_result(stream) or "").strip()
        if txt and txt != heard_text:
            # the recognizer produced MORE words -> reset the silence clock
            heard_text = txt
            last_new_word_t = time.time()
        # Close only after real words were heard and then NO new words for SILENCE_MS.
        if heard_text and last_new_word_t is not None:
            if (time.time() - last_new_word_t) * 1000.0 >= SILENCE_MS:
                break
        if not got:
            time.sleep(0.01)
        # false wake: no words at all after 3s
        if (not heard_text) and (time.time() - t0 > 3.0):
            break
    try:
        stream.input_finished()
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        t2 = rec.get_result(stream)
        if t2: heard_text = t2
    except Exception:
        pass
    STATE["_sr_text"] = (heard_text or "").strip()
    return np.concatenate(frames) if frames else np.array([], dtype=np.int16)


# --- hot-swappable mic control (works while running) -------------------------
MIC_CTL = {
    "index": FORCE_MIC_INDEX,   # None = auto (best-first)
    "generation": 0,            # bumped to force the capture loop to re-open
}
MIC_CTL_LOCK = threading.Lock()


def mic_ctl_generation():
    with MIC_CTL_LOCK:
        return MIC_CTL["generation"]


def set_mic_index(index):
    """Pin a specific input device (int) or None for auto/best-first.
    Persists to spark_config.json so it survives a restart; takes effect
    immediately in the running loop (no restart needed)."""
    with MIC_CTL_LOCK:
        MIC_CTL["index"] = index
        MIC_CTL["generation"] += 1
    with LOCK:
        STATE["mic_pinned"] = index
    try:
        path = os.path.join(ROOT, "spark_config.json")
        data = {}
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f) or {}
        data["force_mic_index"] = index
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        add_event("ERROR", f"mic cfg save: {e}")


def list_inputs():
    """All capturable input devices: [{index,name,channels,current}], best-first
    (same ordering the auto picker uses) so the UI shows the real priority."""
    pa = pyaudio.PyAudio()
    out = []; seen = set()
    try:
        cur = STATE.get("mic_index")
        for i in input_candidates(pa):
            ok, nm = _valid_input(pa, i)
            if not ok or i in seen:
                continue
            seen.add(i)
            try:
                ch = pa.get_device_info_by_index(i).get("maxInputChannels", 0)
            except Exception:
                ch = 0
            out.append({"index": i, "name": nm, "channels": ch, "current": (i == cur)})
    finally:
        pa.terminate()
    return out


def _open_input(pa, CH):
    """Open the best available input honoring the current pin, best-first.
    Returns (stream, idx, name) or (None, None, '')."""
    with MIC_CTL_LOCK:
        pin = MIC_CTL["index"]
    cands = input_candidates(pa)
    if pin is not None:
        cands = [pin] + [c for c in cands if c != pin]
    for i in cands:
        ok, nm = _valid_input(pa, i)
        if not ok:
            continue
        try:
            st = pa.open(format=pyaudio.paInt16, channels=1, rate=NATIVE,
                         input=True, input_device_index=i, frames_per_buffer=CH)
            return st, i, nm
        except Exception as e:
            add_event("WARN", f"mic [{i}] {nm} failed: {e}")
    return None, None, ""


def _device_signature(pa):
    """Signature of the capturable-input device set (index:name), so a
    plug/unplug is detected even when the device count stays the same."""
    sig = []
    for i in range(pa.get_device_count()):
        try:
            d = pa.get_device_info_by_index(i)
        except Exception:
            continue
        if d.get("maxInputChannels", 0) > 0:
            sig.append(f"{i}:{d.get('name','')}")
    return "|".join(sig)


def mic_loop():
    pa = pyaudio.PyAudio()
    model = Model(wakeword_models=[WAKE], inference_framework="onnx")
    CH = int(NATIVE*FRAME_MS/1000)
    while True:                       # outer: (re)open the device when needed
        stream, idx, name = _open_input(pa, CH)
        if stream is None:
            add_event("ERROR", "no usable input device found")
            with LOCK:
                STATE["mic_index"] = None; STATE["mic_name"] = "(none)"
            time.sleep(1.0)
            continue
        with LOCK:
            STATE["mic_index"] = idx; STATE["mic_name"] = name
        add_event("INFO", f"listening for wakeword '{os.path.basename(WAKE).replace('.onnx','').replace('_',' ')}'")
        _sig = _device_signature(pa)
        _gen = mic_ctl_generation()
        _last_scan = 0.0
        while True:                   # inner: read from the currently open device
            try:
                raw = stream.read(CH, exception_on_overflow=False)
                a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(a*a)))
                _pred = model.predict(resample(a, NATIVE))
                score = float(max(_pred.values()) if _pred else 0.0)
                with LOCK:
                    STATE["frames"] += 1
                    STATE["rms"] = rms; STATE["rms_peak"] = max(STATE["rms_peak"], rms)
                    STATE["score"] = score; STATE["score_peak"] = max(STATE["score_peak"], score)
                if score >= THRESH:
                    add_event("WAKE", f"score={score:.2f}")
                    model.reset()
                    _INTERRUPT.clear()                 # fresh turn: reset the Stop latch
                    with LOCK: STATE["state"] = "listening"
                    # Play the cue FIRST and let it finish, THEN start listening.
                    # Nothing is buffered during the cue, so it can never be recorded
                    # and no audio is thrown away by guesswork.
                    # Start the mic buffer the instant the wakeword fires, BEFORE the cue,
                    # then start the recognizer right away too, and play the cue WHILE
                    # capture is already running. Nothing said is ever missed.
                    mkbuf = MicBuffer(stream, CH).start()
                    _tcap = time.time()
                    _ack = threading.Thread(target=speak_ack, daemon=True)
                    _ack.start()
                    utt = capture_from(mkbuf)
                    _ack.join(timeout=0.1)
                    mkbuf.halt()
                    add_event("INFO", f"capture took {time.time()-_tcap:.2f}s")
                    if len(utt) < RATE * 0.3:
                        with LOCK: STATE["state"] = "idle"
                        continue
                    with LOCK: STATE["state"] = "transcribing"
                    p = tempfile.mktemp(suffix=".wav")
                    # CRITICAL: force int16. If utt is float32, tobytes() writes raw
                    # floats as if they were PCM, producing audio with energy but no speech.
                    _utt16 = np.asarray(utt).astype(np.int16)
                    with wave.open(p, "wb") as w:
                        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
                        w.writeframes(_utt16.tobytes())
                    text = transcribe_audio(p)
                    with LOCK: STATE["last_transcript"] = text
                    add_event("STT", text or "(empty)")
                    low = (text or "").lower()
                    # Noise guard: a transcript that is ONLY a bracketed tag like
                    # "(bell ding)" / "(music)" is non-speech — ignore it entirely
                    # (no log, no agent call, no credits).
                    if is_noise_only(text):
                        play_cached("missed")        # error.wav cue
                        with LOCK: STATE["state"] = "idle"
                        continue
                    log_turn(text)
                    if (not text) or "[blank_audio]" in low or low.strip(" .!?,") == "":
                        add_event("INFO", "blank/empty transcript - ignored")
                        play_cached("missed")
                        with LOCK: STATE["state"] = "idle"
                        continue
                    # (single-word guard removed per request)
                    if CONFIRM_WORD and CONFIRM_WORD not in text.lower():
                        with LOCK: STATE["state"] = "idle"
                        continue
                    # --- extensions: watcher routes to tools, hands output to the agent ---
                    # Extensions NEVER speak and NEVER consume the turn: the
                    # watcher runs the tools, then the result (original words +
                    # 'handled' note + tool output) goes to OpenClaw, which does
                    # all the phrasing. TTS reads OpenClaw's reply.
                    if BRIDGE:
                        text, _wres, _wroute = BRIDGE.process(text)
                    else:
                        _wres = None
                    # Fast path (opt-in per skill): every action ran a `fast`
                    # skill and no prose was asked, so the tool's own output is
                    # spoken directly and the agent round-trip is skipped.
                    _fast = _wres.get("fast_speech") if isinstance(_wres, dict) else None
                    with LOCK: STATE["state"] = "thinking"
                    if _fast:
                        reply = _fast
                    else:
                        start_thinking_loop()      # hum evenly while we wait
                        reply = ask_spark(text)
                        stop_thinking_loop()       # finish the current hum first
                    # Stop pressed mid-turn: abandon the reply, go straight back to idle.
                    if _INTERRUPT.is_set() or not reply:
                        with LOCK: STATE["state"] = "idle"
                        continue
                    play_cached("done")            # the higher note lands with the answer
                    with LOCK:
                        STATE["last_reply"] = reply
                        STATE["state"] = "speaking"
                    add_event("REPLY", reply or "(empty)")
                    speak(reply)
                    with LOCK: STATE["state"] = "idle"
            except Exception as e:
                add_event("ERROR", str(e)); time.sleep(0.5)

            # Re-open when the device set changes (plug/unplug) or a switch was
            # requested from the dashboard. Checked on a timer, never mid-turn,
            # so a switch can never clip a reply.
            if time.time() - _last_scan > 3.0:
                _last_scan = time.time()
                try:
                    _nsig = _device_signature(pa)
                except Exception:
                    _nsig = _sig
                if _nsig != _sig or mic_ctl_generation() != _gen:
                    add_event("INFO", f"mic change detected - reopening device [{idx}] {name}")
                    break
                _sig = _nsig
            time.sleep(0.003)
        try:
            stream.stop_stream(); stream.close()
        except Exception:
            pass


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Spark dashboard</title>
<style>
:root{--bg:#0a0e14;--fg:#d7e3f4;--cy:#22d3ee;--gr:#34d399;--am:#fbbf24;--rd:#f87171;--pan:#111826;}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
background:var(--bg);color:var(--fg);padding:24px}
h1{font-size:18px;margin:0 0 4px;color:var(--cy)}.sub{color:#7c8aa5;margin-bottom:20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:900px}
.pan{background:var(--pan);border:1px solid #1e293b;border-radius:12px;padding:16px}
.lbl{color:#7c8aa5;font-size:12px;text-transform:uppercase;letter-spacing:.08em}
.val{font-size:26px;margin:6px 0}
.bar{height:14px;background:#0d1420;border-radius:7px;overflow:hidden;margin-top:8px}
.fill{height:100%;width:0;transition:width .08s;background:linear-gradient(90deg,#0891b2,#22d3ee)}
.wide{grid-column:1/-1}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:4px 8px;border-bottom:1px solid #16202f}
.k{color:#7c8aa5}.WAKE{color:var(--gr)}.ERROR{color:var(--rd)}.INFO{color:#7c8aa5}
.STT{color:var(--am)}.REPLY{color:var(--gr)}
.tsk-new{color:var(--am)}
.tk-st{padding:1px 6px;border-radius:5px;font-size:11px;border:1px solid #1e293b;color:#7c8aa5}
.tk-st.pending{color:var(--cy);border-color:#155e75}
.tk-st.completed{color:var(--gr);border-color:#065f46}
.tk-st.cancelled{color:var(--rd);border-color:#7f1d1d}
.rownew{animation:hl 3s ease-out}
@keyframes hl{0%{background:#1d2a1f}100%{background:transparent}}
.btn{appearance:none;border:1px solid #1e293b;background:#0d1420;color:var(--cy);font:inherit;font-size:12px;padding:5px 14px;border-radius:8px;cursor:pointer;transition:background .15s,border-color .15s,color .15s;margin-left:10px;vertical-align:middle}
.btn:hover{background:#132033;border-color:var(--cy)}
.btn:active{transform:translateY(1px)}
.btn.done{color:var(--gr);border-color:var(--gr)}
#stopbtn{color:#f87171;border-color:#7f1d1d;background:#1a0d0d}
#stopbtn:hover{color:#fff;background:#dc2626;border-color:#ef4444;box-shadow:0 0 10px #ef4444,0 0 20px #ef444488}
#stopbtn:active{transform:translateY(1px);box-shadow:0 0 6px #ef4444}
#stopbtn.done{color:#fff;background:#b91c1c;border-color:#ef4444;box-shadow:0 0 8px #ef4444}
.copystat{font-size:11px;color:var(--gr);margin-left:8px;opacity:0;transition:opacity .2s}
.copystat.show{opacity:1}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--gr);
margin-right:8px;animation:p 1.4s infinite}@keyframes p{50%{opacity:.35}}
</style></head><body>
<h1><span class=dot></span>⚡ SPARK — voice pipeline</h1>
<div class=sub id=mic>connecting…</div>
<div class=pan wide style="margin-bottom:16px"><div class=lbl>microphone <span id=micpin class=sub style="text-transform:none;letter-spacing:0"></span></div>
  <select id=micpick class=btn style="margin-left:0;padding:5px 10px"></select>
  <span class=copystat id=micstat></span></div>
<div class=grid>
  <div class=pan><div class=lbl>mic level (rms)</div><div class=val id=rms>0</div>
    <div class=bar><div class=fill id=rmsfill></div></div>
    <div class=sub id=rmsp>peak 0</div></div>
  <div class=pan><div class=lbl>wakeword score</div><div class=val id=score>0.00</div>
    <div class=bar><div class=fill id=scorefill></div></div>
    <div class=sub id=scorep>peak 0.00</div></div>
  <div class=pan wide><div class=lbl>pipeline<button class=btn id=stopbtn>Stop</button><span class=copystat id=stopstat></span></div>
    <div class=val id=stage>idle</div>
    <div class=sub>last heard: <span id=tr>—</span></div>
    <div class=sub>spark said: <span id=rp>—</span></div></div>
  <div class=pan wide><div class=lbl>recent events<button class=btn id=copyev>Copy events</button><span class=copystat id=copystat></span></div>
    <div id=evwrap style="user-select:text;-webkit-user-select:text"><table id=ev></table></div></div>
  <div class=pan wide><div class=lbl>tasks <span id=taskcount class=sub style="text-transform:none;letter-spacing:0"></span></div>
    <table id=tk></table></div>
  <div class=pan wide><div class=lbl>extensions <span id=extcount class=sub style="text-transform:none;letter-spacing:0"></span></div>
    <table id=ext></table></div>
  <div class=pan wide><div class=lbl>watcher routes <span id=routestat class=sub style="text-transform:none;letter-spacing:0"></span></div>
    <table id=routes></table></div>
  <div class=pan wide><div class=sub id=stat></div></div>
</div>
<script>
async function tick(){
  try{
    const r=await fetch('/state?'+Date.now(),{cache:'no-store'}); const s=await r.json();
    document.getElementById('mic').textContent='mic ['+s.mic_index+'] '+s.mic_name
      +'  ·  uptime '+Math.round((Date.now()/1000-(s.started||0)))+'s'
      +'  ·  frames '+s.frames+'  ·  last wake '+(s.last_wake||'—');
    document.getElementById('rms').textContent=s.rms.toFixed(1);
    document.getElementById('rmsfill').style.width=Math.min(100,s.rms/300*100)+'%';
    document.getElementById('rmsp').textContent='peak '+s.rms_peak.toFixed(0);
    document.getElementById('score').textContent=s.score.toFixed(2);
    document.getElementById('scorefill').style.width=Math.min(100,s.score*100)+'%';
    document.getElementById('scorep').textContent='peak '+s.score_peak.toFixed(2);
    document.getElementById('stage').textContent=s.state||'idle';
    document.getElementById('tr').textContent=s.last_transcript||'—';
    document.getElementById('rp').textContent=s.last_reply||'—';
    const evHtml=s.events.slice().reverse().map(e=>
      '<tr><td class=k>'+e.t+'</td><td class='+e.kind+'>'+e.kind+'</td><td>'+e.detail+'</td></tr>').join('');
    const evEl=document.getElementById('ev');
    if(evEl._html!==evHtml){ evEl.innerHTML=evHtml; evEl._html=evHtml; }
    evEl._text=s.events.slice().reverse().map(e=>e.t+'  '+e.kind+'  '+e.detail).join(String.fromCharCode(10));
    document.getElementById('stat').textContent='serving on http://127.0.0.1:8770';
    if(!_micInit){ try{ await loadMics(); }catch(e){} }
  }catch(e){document.getElementById('mic').textContent='disconnected: '+(e&&e.message?e.message:e);}
  try{
    const r2=await fetch('/tasks?'+Date.now(),{cache:'no-store'}); const t=await r2.json();
    document.getElementById('taskcount').textContent=t.today+' new today · '+t.count+' total';
    const seen=window._seenTasks||(window._seenTasks={});
    let html='';
    for(const x of (t.tasks||[])){
      const isNew=!seen[x.task_id] && window._tasksInit; seen[x.task_id]=1;
      html+='<tr class="'+(isNew?'rownew':'')+'">'
        +'<td class=k>#'+x.task_id+'</td>'
        +'<td>'+String(x.content||'').replace(/</g,'&lt;')+'</td>'
        +'<td class=k>'+String(x.trigger_time||'')+'</td>'
        +'<td><span class="tk-st '+x.status+'">'+x.status+'</span></td>'
        +'<td class=k>'+String(x.created_at||'').slice(5,16)+'</td></tr>';
    }
    const el=document.getElementById('tk');
    if(el._html!==html){ el.innerHTML=html||'<tr><td class=k>no tasks yet</td></tr>'; el._html=html; }
    window._tasksInit=true;
  }catch(e){}
  try{
    const r3=await fetch('/skills?'+Date.now(),{cache:'no-store'}); const x=await r3.json();
    document.getElementById('extcount').textContent=x.tool_count+' tools · '+(x.extensions||[]).length+' extensions';
    let xh='';
    for(const e of (x.extensions||[])){
      const st=e.error?'<span class="tk-st cancelled">failed</span>'
        :(e.active?'<span class="tk-st completed">active</span>'
        :(e.enabled?'<span class="tk-st pending">idle</span>':'<span class="tk-st">off</span>'));
      const scopes=(e.scopes===null||e.scopes===undefined)?'*':(e.scopes||[]).join(',')||'—';
      xh+='<tr><td>'+String(e.id).replace(/</g,'&lt;')+'</td>'
        +'<td class=k>v'+String(e.version||'')+'</td>'
        +'<td>'+st+'</td>'
        +'<td class=k>'+String(scopes).replace(/</g,'&lt;')+'</td>'
        +'<td class=k>'+String((e.hooks||[]).join(',')).replace(/</g,'&lt;')+'</td>'
        +'<td class=k>'+String(e.error||'').replace(/</g,'&lt;')+'</td></tr>';
    }
    const xe=document.getElementById('ext');
    if(xe._html!==xh){ xe.innerHTML=xh||'<tr><td class=k>no extensions loaded</td></tr>'; xe._html=xh; }
  }catch(e){}
  try{
    const r4=await fetch('/routes?'+Date.now(),{cache:'no-store'}); const rr=await r4.json();
    const stats=rr.stats||{};
    document.getElementById('routestat').textContent=(stats.total||0)+' routes · '+(stats.ok||0)+' ok · '+(stats.fail||0)+' fail'+(stats.bad_rate!==undefined?' ('+(stats.bad_rate)+'% bad)':'');
    let rh='';
    for(const x of (rr.routes||[])){
      const st=x.ok?'<span class="tk-st completed">ok</span>':'<span class="tk-st cancelled">fail</span>';
      const acts=(x.actions||[]).length?(x.actions||[]).join('+'):'—';
      const fails=(x.failures||[]).length?(x.failures||[]).join(','):'';
      rh+='<tr><td class=k>'+String(x.isa||'').slice(5,16)+'</td>'
        +'<td>'+String(x.text||'').replace(/</g,'&lt;')+'</td>'
        +'<td class=k>'+String(x.intent||'').replace(/</g,'&lt;')+'</td>'
        +'<td class=k>'+acts.replace(/</g,'&lt;')+'</td>'
        +'<td>'+st+(fails?' <span class=k>'+fails.replace(/</g,'&lt;')+'</span>':'')+'</td></tr>';
    }
    const re=document.getElementById('routes');
    if(re._html!==rh){ re.innerHTML=rh||'<tr><td class=k>no routes logged yet</td></tr>'; re._html=rh; }
  }catch(e){}
}
let _micInit=false;
async function loadMics(){
  const r=await fetch('/mics?'+Date.now(),{cache:'no-store'}); const m=await r.json();
  const sel=document.getElementById('micpick');
  let html='<option value="auto">auto (best available)</option>';
  for(const d of (m.mics||[])){
    html+='<option value="'+d.index+'">['+d.index+'] '+String(d.name).replace(/</g,'&lt;')+(d.current?'  (active)':'')+'</option>';
  }
  if(sel._html!==html){ sel.innerHTML=html; sel._html=html; }
  const pin=(m.pinned===null||m.pinned===undefined)?'auto':String(m.pinned);
  if(sel.value!==pin) sel.value=pin;
  document.getElementById('micpin').textContent=(m.pinned===null||m.pinned===undefined)?'(auto)':'(pinned '+m.pinned+')';
  _micInit=true;
}
const _mp=document.getElementById('micpick');
_mp.addEventListener('change',async()=>{
  const v=_mp.value; const st=document.getElementById('micstat');
  try{ await fetch('/mic',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({index:v==='auto'?null:parseInt(v,10)})});
    st.textContent='switching…'; st.classList.add('show');
  }catch(e){ st.textContent='switch failed'; st.classList.add('show'); }
  setTimeout(()=>{ st.classList.remove('show'); },1800);
  try{ await loadMics(); }catch(e){}
});
const _sb=document.getElementById('stopbtn');
_sb.addEventListener('click',async()=>{
  const st=document.getElementById('stopstat');
  try{ await fetch('/stop?'+Date.now(),{cache:'no-store'}); _sb.textContent='Stopped'; _sb.classList.add('done'); st.textContent='thinking dropped, playback halted'; st.classList.add('show'); }
  catch(e){ _sb.textContent='Stop failed'; _sb.classList.add('done'); st.textContent=''; st.classList.remove('show'); }
  setTimeout(()=>{ _sb.textContent='Stop'; _sb.classList.remove('done'); st.classList.remove('show'); },1400);
});
const _cb=document.getElementById('copyev');
_cb.addEventListener('click',async()=>{
  const t=document.getElementById('ev')._text||'';
  const st=document.getElementById('copystat');
  let ok=false;
  try{ await navigator.clipboard.writeText(t); ok=true; }
  catch(e){ const ta=document.createElement('textarea'); ta.value=t; ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta); ta.select(); try{document.execCommand('copy');ok=true;}catch(_){} ta.remove(); }
  _cb.textContent = ok ? 'Copied' : 'Copy failed';
  _cb.classList.toggle('done', ok);
  st.textContent = ok ? (String(t).split(String.fromCharCode(10)).length+' lines') : '';
  st.classList.toggle('show', ok);
  setTimeout(()=>{ _cb.textContent='Copy events'; _cb.classList.remove('done'); st.classList.remove('show'); },1600);
});
setInterval(tick,300);tick();
</script></body></html>"""

# --- task DB (read-only) so the dashboard can show new tasks ---
TASKS_DB = _cfg("tasks_db", os.path.join(ROOT, "data", "openmemo.db"))

def read_routes(limit=40):
    """Newest watcher routes from voice_routes.jsonl (read-only), plus pass/fail
    stats over the whole file. Each entry is summarized for the dashboard."""
    path = os.path.join(ROOT, "voice_routes.jsonl")
    if not os.path.exists(path):
        return [], {"total": 0, "ok": 0, "fail": 0, "bad_rate": 0}
    total = ok = fail = 0
    tail = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                total += 1
                handled = [h for h in (r.get("handled") or []) if h]
                failures = [x for x in (r.get("failures") or []) if x]
                good = bool(handled) and not failures
                if good:
                    ok += 1
                elif failures:
                    fail += 1
                tail.append({
                    "isa": r.get("isa") or "",
                    "text": r.get("text") or "",
                    "intent": r.get("intent") or "",
                    "actions": [a for a in (r.get("actions") or []) if a],
                    "handled": handled,
                    "failures": failures,
                    "ok": good,
                })
    except Exception:
        return [], {"total": 0, "ok": 0, "fail": 0, "bad_rate": 0}
    stat = {"total": total, "ok": ok, "fail": fail,
            "bad_rate": round(100.0 * fail / total, 1) if total else 0}
    return tail[-limit:][::-1], stat


def read_tasks(limit=60):
    """Newest tasks from the scheduler DB (read-only). Returns (tasks, today_count)."""
    if not os.path.exists(TASKS_DB):
        return [], 0
    try:
        conn = sqlite3.connect(TASKS_DB, timeout=2)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT task_id,content,trigger_time,status,priority,created_at,reminder_sent,is_recurring "
            "FROM tasks WHERE deleted_at IS NULL "
            "ORDER BY created_at DESC, task_id DESC LIMIT ?", (limit,)).fetchall()
        today = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE deleted_at IS NULL AND substr(created_at,1,10)=date('now','localtime')"
        ).fetchone()[0]
        conn.close()
        return [dict(r) for r in rows], today
    except Exception:
        return [], 0


def say_external(text):
    """Speak exact text on request from another process (e.g. the task engine's
    reminders). Sets state to 'speaking' so the music ducks, then back to idle.
    Blocks until speech finishes so the caller can order several lines."""
    if not text or _INTERRUPT.is_set():
        return False
    with LOCK: STATE["state"] = "speaking"
    try:
        speak(text)
    except Exception as e:
        add_event("ERROR", f"say: {e}")
    finally:
        with LOCK: STATE["state"] = "idle"
    return True


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/mic":
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b""
                payload = json.loads(raw.decode() or "{}")
                idx = payload.get("index")
            except Exception:
                idx = None
                payload = {}
            if isinstance(idx, str) and idx.strip().lower() in ("", "auto", "none", "null"):
                idx = None
            elif idx is not None:
                try:
                    idx = int(idx)
                except (TypeError, ValueError):
                    idx = None
            set_mic_index(idx)
            body = json.dumps({"ok": True, "index": idx}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
            return
        if path == "/say":
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b""
                text = (json.loads(raw.decode() or "{}").get("text") or "").strip()
            except Exception:
                text = ""
            if text:
                say_external(text)
                body = b'{"ok":true}'
                self.send_response(200)
            else:
                body = b'{"ok":false,"error":"no text"}'
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
            return
        body = b'{"ok":false,"error":"not found"}'
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/mics":
            body = json.dumps({"mics": list_inputs(),
                               "pinned": STATE.get("mic_pinned"),
                               "current": STATE.get("mic_index")}).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if path == "/mic":
            q = urllib.parse.urlparse(self.path).query
            idx = urllib.parse.parse_qs(q).get("index", [None])[0]
            if idx is None or str(idx).strip().lower() in ("", "auto", "none", "null"):
                set_mic_index(None)
            else:
                try:
                    set_mic_index(int(idx))
                except (TypeError, ValueError):
                    pass
            body = b'{"ok":true}'
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if path == "/stop":
            do_stop()
            body = b'{"ok":true}'
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if path == "/tasks":
            tasks, today = read_tasks()
            body = json.dumps({"tasks": tasks, "count": len(tasks), "today": today}).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if path == "/skills":
            exts = EXT_MGR.status() if EXT_MGR is not None else []
            tool_map = {}
            if EXT_REG is not None:
                for s in EXT_REG.catalog():
                    tool_map.setdefault(s.get("name"), s.get("args") or {})
            body = json.dumps({
                "extensions": exts,
                "tool_count": len(tool_map),
                "tools": {k: sorted(v.keys()) for k, v in tool_map.items()},
            }).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if path == "/routes":
            routes, stats = read_routes()
            body = json.dumps({"routes": routes, "stats": stats}).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if path == "/state":
            with LOCK:
                body = json.dumps({k:v for k,v in STATE.items() if k!="events"} | {"events":STATE["events"]})
            body = body.encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma","no-cache"); self.send_header("Expires","0")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
        else:
            body = PAGE.encode()
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma","no-cache"); self.send_header("Expires","0")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)

def init_extensions():
    """Load extensions and start the watcher router. Guarded: any failure just
    leaves EXT_MGR/BRIDGE as None and the pipeline behaves as before."""
    global EXT_MGR, BRIDGE, EXT_REG
    try:
        from sparkext import Manager, Registry, Watcher, Bridge
        EXT_MGR = Manager(
            root=os.path.join(ROOT, "extensions"),
            data_root=os.path.join(ROOT, "data", "extensions"),
            host={"emit": add_event, "speak": speak, "ask_agent": ask_spark},
            emit=add_event)
        EXT_MGR.load_all(force=True)
        reg = Registry(emit=add_event).collect_from(EXT_MGR)
        EXT_REG = reg
        # Router LLM: primary + optional backups (tried if the primary errors).
        _backups = _cfg("watcher_backup_models", []) or []
        if isinstance(_backups, str):
            _backups = [m.strip() for m in _backups.split(",") if m.strip()]
        watcher = Watcher(model=_cfg("watcher_model", "qwen2.5:7b"),
                          timeout=15.0, backups=_backups)
        if watcher.available():
            # warm the local model so the first real turn is not slow
            threading.Thread(target=lambda: watcher.route("warm up", reg.catalog()),
                             daemon=True).start()
        BRIDGE = Bridge(EXT_MGR, reg, watcher,
                        log_path=os.path.join(ROOT, "voice_routes.jsonl"),
                        emit=add_event, history_size=6)
        add_event("INFO", f"extensions: {[s['name'] for s in reg.catalog()]}")
        _start_ext_reload(EXT_MGR)
    except Exception as e:
        EXT_MGR = None; BRIDGE = None
        add_event("ERROR", f"extensions init: {e}")


def _start_ext_reload(mgr):
    """Hot-reload extension folders on a timer; refresh the skill catalog."""
    def _loop():
        while True:
            time.sleep(6.0)
            try:
                if mgr.reload_tick() and BRIDGE is not None:
                    from sparkext import Registry
                    global EXT_REG
                    new_reg = Registry(emit=add_event).collect_from(mgr)
                    BRIDGE.registry = new_reg
                    EXT_REG = new_reg
            except Exception:
                pass
    threading.Thread(target=_loop, daemon=True).start()


if __name__ == "__main__":
    prewarm()
    init_extensions()
    threading.Thread(target=mic_loop, daemon=True).start()
    print(f"Spark web dashboard: http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
