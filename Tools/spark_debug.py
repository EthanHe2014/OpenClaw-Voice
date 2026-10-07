#!/usr/bin/env python3
"""
Spark debug dashboard — live view of the voice pipeline.

Shows:
  - mic RMS level (bar)
  - wakeword score (bar) 
  - recent events (wake detected, transcript, reply)

Run: ./.venv/bin/python spark_debug.py
"""
import os, sys, time, subprocess, tempfile, threading, wave
import numpy as np
os.environ.setdefault("https_proxy", "http://127.0.0.1:7897")
os.environ.setdefault("http_proxy", "http://127.0.0.1:7897")
os.environ.setdefault("all_proxy", "socks5://127.0.0.1:7897")
import pyaudio
from openwakeword.model import Model

MIC_SUBSTR = "USB PnP"; FALLBACK = 2
NATIVE = 48000; RATE = 16000; FRAME_MS = 80
WAKE = "hey_jarvis_v0.1"; THRESH = 0.5

def bar(v, width=30, mx=1.0):
    n = int(max(0, min(1, v/mx)) * width)
    return "█"*n + "·"*(width-n)

def cls():
    sys.stdout.write("\033[2J\033[H"); sys.stdout.flush()

def find_mic(pa):
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d.get("maxInputChannels",0) > 0 and MIC_SUBSTR.lower() in d.get("name","").lower():
            return i, d.get("name")
    return FALLBACK, "(fallback)"

def resample(a, src):
    if src == RATE: return a
    n = int(len(a)*RATE/src)
    return np.interp(np.linspace(0, len(a)-1, n), np.arange(len(a)), a).astype(np.int16)

pa = pyaudio.PyAudio()
idx, name = find_mic(pa)
model = Model(wakeword_models=[WAKE], inference_framework="onnx")
CH = int(NATIVE*FRAME_MS/1000)
stream = pa.open(format=pyaudio.paInt16, channels=1, rate=NATIVE,
                 input=True, input_device_index=idx, frames_per_buffer=CH)
events = []
peak_rms = 0.0; peak_score = 0.0; frames = 0
print("Spark debug dashboard — Ctrl-C to quit")
try:
    while True:
        raw = stream.read(CH, exception_on_overflow=False)
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(a*a)))
        a16 = resample(a, NATIVE)
        score = float(model.predict(a16).get(WAKE, 0.0))
        peak_rms = max(peak_rms, rms); peak_score = max(peak_score, score); frames += 1
        if score >= THRESH:
            events.append(time.strftime("%H:%M:%S") + f"  WAKE  score={score:.2f}")
            model.reset()
        events = events[-8:]
        cls()
        print("⚡ SPARK DEBUG DASHBOARD\n")
        print(f"mic       : [{idx}] {name}")
        print(f"frames    : {frames}   uptime {frames*FRAME_MS/1000:0.0f}s")
        print()
        print(f"mic RMS   : {bar(rms, 40, 300)}  {rms:7.1f}  (peak {peak_rms:0.0f})")
        print(f"wake score: {bar(score, 40, 1.0)}  {score:5.2f}  (peak {peak_score:2.2f})")
        print()
        print("recent events:")
        for e in events or ["  (none yet)"]:
            print("  " + e)
        time.sleep(0.05)
except KeyboardInterrupt:
    print("\nbye")
finally:
    stream.stop_stream(); stream.close(); pa.terminate()
