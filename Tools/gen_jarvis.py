import asyncio, edge_tts, os
os.environ.setdefault("https_proxy","http://127.0.0.1:7897")
os.environ.setdefault("http_proxy","http://127.0.0.1:7897")
os.environ.setdefault("all_proxy","socks5://127.0.0.1:7897")
async def main():
    await edge_tts.Communicate("Hey Jarvis", "en-US-AriaNeural").save("/tmp/hey_jarvis.wav")
asyncio.run(main())
print("size", os.path.getsize("/tmp/hey_jarvis.wav"))
