#!/usr/bin/env python3
"""
Spark voice listener — wakeword -> capture -> STT -> OpenClaw -> TTS.

Pipeline:
  USB mic (PyAudio idx 2, 48k) -> resample 16k -> openWakeWord "hey_jarvis"
  -> on wake: record until silence (energy gate) -> STT -> confirm contains "spark"
  -> send text to OpenClaw (Spark agent) -> get reply -> edge-tts speak (fallback: say)

Run: ./.venv/bin/python spark_listener.py
"""
import os, sys, time, json, subprocess, tempfile, queue, threading, wave, re
import numpy as np

# --- configuration: spark_config.json + SPARK_* env ---
try:
    from spark_config import CFG
except Exception:
    CFG = None
def _cfg(k, d):
    return getattr(CFG, k, d) if CFG is not None else d

_proxy = _cfg("proxy", "")
if _proxy:
    os.environ.setdefault("https_proxy", _proxy)
    os.environ.setdefault("http_proxy", _proxy)
    os.environ.setdefault("all_proxy", _proxy)

import pyaudio
from openwakeword.model import Model

# ---------------- Config ----------------
MIC_NAME_SUBSTR = _cfg("mic_name_substr", "USB PnP")   # match by name; fall back to index
MIC_INDEX_FALLBACK = _cfg("mic_index_fallback", 2)
NATIVE_RATE = _cfg("native_rate", 48000)
RATE = _cfg("rate", 16000)
FRAME_MS = _cfg("frame_ms", 80)
FRAME = int(RATE * FRAME_MS / 1000)          # 1280 samples @16k
WAKE_THRESHOLD = _cfg("wake_threshold", 0.5)
WAKE_MODEL = _cfg("wake_model", "hey_jarvis_v0.1")
ENERGY_GATE = 25.0                            # RMS above this counts as speech
SILENCE_MS = _cfg("silence_ms", 1200)         # stop recording after this much quiet
MAX_UTTER_S = _cfg("max_utter_s", 10.0)
CONFIRM_WORD = _cfg("confirm_word", "spark")  # STT must contain this ("" = off)
WHISPER_BIN = _cfg("whisper_bin", "whisper-cli")
WHISPER_MODEL = _cfg("whisper_model", os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "ggml-base.bin"))
STT_LANG = _cfg("stt_language", "auto")
TTS_VOICE = _cfg("voice", "en-US-AriaNeural")

# ---------------- Mic helpers ----------------
def find_usb_index(pa):
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d.get("maxInputChannels", 0) > 0 and MIC_NAME_SUBSTR.lower() in d.get("name","").lower():
            return i, d.get("name")
    return MIC_INDEX_FALLBACK, "(fallback)"

def resample_to_16k(a, src_rate):
    if src_rate == RATE:
        return a
    n = int(len(a) * RATE / src_rate)
    idx = np.linspace(0, len(a) - 1, n)
    return np.interp(idx, np.arange(len(a)), a).astype(np.int16)

# ---------------- STT (pluggable) ----------------
def transcribe(wav_path):
    """Transcribe a 16k mono wav. Returns text ('' on failure)."""
    return stt_backend(wav_path)

def stt_backend(wav_path):
    """whisper.cpp -> text."""
    try:
        r = subprocess.run(
            [WHISPER_BIN, "-m", WHISPER_MODEL, "-f", wav_path,
             "-l", STT_LANG, "-nt", "-np"],
            capture_output=True, text=True, timeout=30)
        return (r.stdout or "").strip()
    except Exception as e:
        print("whisper err:", e)
        return ""

# ---------------- TTS ----------------
def speak(text):
    """edge-tts -> fallback macOS say. Never fatal."""
    if not text:
        return
    voice = "zh-CN-XiaoxiaoNeural" if re.search(r"[\u4e00-\u9fff]", text) else "en-US-AriaNeural"
    out = tempfile.mktemp(suffix=".wav")
    try:
        import asyncio, edge_tts
        async def _gen():
            await edge_tts.Communicate(text, voice).save(out)
        done = {"ok": False}
        def run():
            try:
                asyncio.run(_gen()); done["ok"] = True
            except Exception as e:
                print("edge-tts err:", e)
        t = threading.Thread(target=run); t.start(); t.join(timeout=8)
        if done["ok"] and os.path.exists(out) and os.path.getsize(out) > 0:
            subprocess.run(["/opt/homebrew/bin/ffplay","-nodisp","-autoexit","-loglevel","quiet", out])
            return
    except Exception as e:
        print("edge-tts failed, falling back to say:", e)
    finally:
        try:
            if os.path.exists(out): os.remove(out)
        except Exception: pass
    subprocess.run(["/usr/bin/say", text])

# ---------------- OpenClaw hand-off ----------------
INBOX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_inbox.jsonl")
OUTBOX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_outbox")

def ask_openclaw(text, timeout=20.0):
    """Hand text to the Spark agent via a file queue and wait for the reply.

    Writes a request line to voice_inbox.jsonl; the Spark agent (or a watcher)
    writes the reply to voice_outbox/<id>.txt; we read it back.
    Falls back to a canned line if no reply arrives in time.
    """
    import uuid, json
    req_id = str(uuid.uuid4())
    line = json.dumps({"id": req_id, "text": text, "ts": time.time()})
    with open(INBOX, "a") as f:
        f.write(line + "\n")
    os.makedirs(OUTBOX, exist_ok=True)
    reply_path = os.path.join(OUTBOX, req_id + ".txt")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(reply_path):
            try:
                with open(reply_path) as f:
                    r = f.read().strip()
                os.remove(reply_path)
                if r:
                    return r
            except Exception:
                pass
        time.sleep(0.25)
    return "Sorry, I didn't get an answer in time."

# ---------------- Capture after wake ----------------
def capture_utterance(stream, pa):
    """Record until silence. Returns 16k int16 array."""
    frames = []
    quiet_ms = 0
    speech_seen = False
    t0 = time.time()
    while time.time() - t0 < MAX_UTTER_S:
        raw = stream.read(FRAME, exception_on_overflow=False)
        a16 = np.frombuffer(raw, dtype=np.int16)          # native 48k mono
        a = resample_to_16k(a16.astype(np.float32), NATIVE_RATE).astype(np.int16)
        rms = float(np.sqrt(np.mean(a.astype(np.float32) ** 2)))
        frames.append(a)
        if rms > ENERGY_GATE:
            speech_seen = True; quiet_ms = 0
        elif speech_seen:
            quiet_ms += FRAME_MS
            if quiet_ms >= SILENCE_MS:
                break
    return np.concatenate(frames) if frames else np.array([], dtype=np.int16)

def save_wav(a, path):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(a.tobytes())

# ---------------- Main loop ----------------
def main():
    pa = pyaudio.PyAudio()
    idx, name = find_usb_index(pa)
    print(f"[mic] device {idx} = {name}")
    model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=NATIVE_RATE,
                     input=True, input_device_index=idx, frames_per_buffer=int(NATIVE_RATE*FRAME_MS/1000))
    print(f"[wake] listening for wakeword '{WAKE_MODEL}' ... (Ctrl-C to quit)")
    try:
        while True:
            raw = stream.read(int(NATIVE_RATE*FRAME_MS/1000), exception_on_overflow=False)
            a16 = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            a = resample_to_16k(a16, NATIVE_RATE)
            pred = model.predict(a)
            score = float(pred.get(WAKE_MODEL, 0.0))
            if score >= WAKE_THRESHOLD:
                print(f"[wake] detected ({score:.2f}) — recording...")
                model.reset()
                utt = capture_utterance(stream, pa)
                if len(utt) < RATE * 0.3:
                    print("[skip] too short"); continue
                path = tempfile.mktemp(suffix=".wav"); save_wav(utt, path)
                text = transcribe(path)
                print(f"[stt] {text!r}")
                if CONFIRM_WORD and CONFIRM_WORD not in text.lower():
                    print("[skip] confirm word not present"); continue
                reply = ask_openclaw(text)
                print(f"[reply] {reply}")
                speak(reply)
    except KeyboardInterrupt:
        print("\n[bye]")
    finally:
        stream.stop_stream(); stream.close(); pa.terminate()

if __name__ == "__main__":
    main()
