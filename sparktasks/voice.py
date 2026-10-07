"""语音输出模块 —— Edge TTS + Mac mini 扬声器"""
import asyncio
import os
import re
import subprocess
import hashlib
from pathlib import Path
from .config import tts_voice, AUDIO_DIR

# 要读出来的文字里不允许出现 emoji（Edge TTS 会把 emoji 读出来/读成乱码）
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # 各类表情/符号
    "\U0001F1E6-\U0001F1FF"  # 国旗字母（🇨🇳 等）
    "\u2600-\u27BF"          # 杂项符号/装饰
    "\u2300-\u23FF"          # 技术符号（⏰⏳ 等）
    "\u2B00-\u2BFF"          # 箭头/星
    "\uFE0F"                 # 变体选择符（emoji 修饰）
    "\u2B50\u2764\u2705\u274C\u270B\u2728\u2E3A-\u2E3B"
    "]"
)


def strip_emojis(text: str) -> str:
    """去掉文本里的 emoji（用于 TTS 朗读；显示场景保留原文）。"""
    if not text:
        return ""
    cleaned = _EMOJI_RE.sub("", text)
    # 去 emoji 后可能留下多余空格
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


async def generate_speech(text: str, voice: str = None, rate: str = "+0%") -> Path:
    """Generate speech audio file using Edge TTS.
    
    Args:
        text: Text to speak
        voice: Voice name (default from config)
        rate: Speech rate adjustment (e.g. "-10%" slower, "+10%" faster)
    
    Returns:
        Path to generated audio file
    """
    import edge_tts

    voice = voice or tts_voice()
    # TTS 不读 emoji（Edge TTS 会把 emoji 念出来）
    text = strip_emojis(text)
    if not text:
        return None
    
    # Generate unique filename from text+rate hash
    text_hash = hashlib.md5(f"{text}{rate}".encode()).hexdigest()[:12]
    output_path = AUDIO_DIR / f"tts_{text_hash}.mp3"
    
    # Skip if already generated (cache)
    if output_path.exists():
        return output_path
    
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(output_path))
    
    return output_path


_playback_proc = None  # 当前 afplay 进程（用于打断）


def play_audio(audio_path: Path, volume: float = 1.0):
    """Play audio file on Mac mini speakers using afplay.

    Args:
        audio_path: Path to audio file
        volume: Volume level (0.0 to 1.0)
    """
    global _playback_proc
    if not audio_path.exists():
        print(f"Audio file not found: {audio_path}")
        return

    # 打断上一条还在播的语音（新回复 / 新提醒开始前）
    stop_speaking()

    # Use afplay on macOS
    cmd = ["afplay", str(audio_path)]
    if volume != 1.0:
        cmd.extend(["-v", str(volume)])

    try:
        _playback_proc = subprocess.Popen(cmd)
        _playback_proc.wait()
    except Exception as e:
        print(f"Audio playback error: {e}")
    finally:
        _playback_proc = None


def stop_speaking():
    """立即打断当前播放（用户开始录音 / 新消息到达时调用，防止回声与叠加）"""
    global _playback_proc
    proc = _playback_proc
    _playback_proc = None
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass


def show_notification(title: str, message: str):
    """macOS 通知横幅（右上角弹出）。语音之外的可视提醒，远程/静音时也能看到。"""
    try:
        safe_title = str(title).replace('"', "'")[:60]
        safe_msg = str(message).replace('"', "'")[:200]
        script = (
            f'display notification "{safe_msg}" '
            f'with title "{safe_title}" sound name "Glass"'
        )
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)
    except Exception as e:
        print(f"通知横幅失败: {e}")


async def speak(text: str, voice: str = None, volume: float = 1.0, rate: str = "+0%") -> bool:
    """Generate speech and play it immediately.
    
    Args:
        text: Text to speak
        voice: Voice name
        volume: Volume level
        rate: Speech rate (e.g. "-10%" slower)
    
    Returns:
        True if successful
    """
    try:
        audio_path = await generate_speech(text, voice, rate)
        # Run playback in a thread to not block
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, play_audio, audio_path, volume)
        # 同时弹一个 macOS 通知横幅（右上角），静音/远程时也能看到
        show_notification("OpenMemo 提醒", text)
        return True
    except Exception as e:
        print(f"Speech error: {e}")
        return False


def speak_sync(text: str, voice: str = None, volume: float = 1.0, rate: str = "+0%") -> bool:
    """Synchronous version of speak()"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, speak(text, voice, volume, rate))
                return future.result(timeout=30)
        else:
            return asyncio.run(speak(text, voice, volume, rate))
    except Exception as e:
        print(f"Sync speech error: {e}")
        return False


# Available Chinese voices for Edge TTS
CHINESE_VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",      # Female, warm
    "xiaoyi": "zh-CN-XiaoyiNeural",           # Female, young
    "yunjian": "zh-CN-YunjianNeural",          # Male, mature
    "yunxi": "zh-CN-YunxiNeural",              # Male, young
    "yunxia": "zh-CN-YunxiaNeural",            # Male, child
    "yunyang": "zh-CN-YunyangNeural",          # Male, news anchor
}
