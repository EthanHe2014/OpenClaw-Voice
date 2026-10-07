import pyaudio, numpy as np
pa = pyaudio.PyAudio()
idx = 2
d = pa.get_device_info_by_index(idx)
print("device", idx, d.get("name"), "rate", d.get("defaultSampleRate"))
RATE = 48000; OUT = 16000; CHUNK = 1024
stream = pa.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                 input=True, input_device_index=idx, frames_per_buffer=CHUNK)
print("listening 3s on USB mic (idx 2)...")
peak = 0.0
for i in range(int(RATE/CHUNK*3)):
    buf = stream.read(CHUNK, exception_on_overflow=False)
    a = np.frombuffer(buf, dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(a*a)))
    peak = max(peak, rms)
    if i % 16 == 0:
        print(f"t={i*CHUNK/RATE:0.1f}s rms={rms:8.1f}")
stream.stop_stream(); stream.close(); pa.terminate()
print("PEAK_RMS", round(peak,1))
print("SILENT" if peak < 1.0 else "AUDIO_OK")
