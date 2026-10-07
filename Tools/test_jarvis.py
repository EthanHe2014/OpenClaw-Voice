import openwakeword, numpy as np, subprocess, wave
from openwakeword.model import Model

subprocess.run(["/opt/homebrew/bin/ffmpeg","-y","-v","error","-i","/tmp/hey_jarvis.wav","-ar","16000","-ac","1","-f","wav","/tmp/hey_jarvis_16k.wav"], check=True)
m = Model(wakeword_models=["hey_jarvis_v0.1"], inference_framework="onnx")

def read_wav_16k(path):
    with wave.open(path, "rb") as w:
        fr = w.getframerate(); ch = w.getnchannels()
        data = w.readframes(w.getnframes())
    a = np.frombuffer(data, dtype=np.int16)
    print("rate", fr, "ch", ch, "samples", len(a))
    return a

a = read_wav_16k("/tmp/hey_jarvis_16k.wav")
CH = 1280
scores = []
for i in range(0, len(a)-CH, CH):
    p = m.predict(a[i:i+CH])
    scores.append(float(p.get("hey_jarvis_v0.1", 0.0)))
print("max score", round(max(scores),3) if scores else None)
print("top5", sorted([round(s,3) for s in scores], reverse=True)[:5])
