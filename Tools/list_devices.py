import pyaudio
pa = pyaudio.PyAudio()
for i in range(pa.get_device_count()):
    d = pa.get_device_info_by_index(i)
    if d.get("maxInputChannels",0) > 0:
        print(i, "|", d.get("name"), "| in:", d.get("maxInputChannels"), "| rate:", d.get("defaultSampleRate"))
pa.terminate()
