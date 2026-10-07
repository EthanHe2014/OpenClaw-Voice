"""Spark task inbox — a folder the AI agent drops reminder JSON files into.

When the agent notices the user implied a scheduled event ("I need to be at the
match at 3"), it silently writes ONE json file into voice_inbox/ WITHOUT telling
the user (unless the user explicitly asked for a reminder). This module scans the
folder every INBOX_POLL_SECONDS, writes each entry into the task DB, schedules
it, and moves the file to voice_inbox/processed/.

When a task fires, the exact English words in its `speak` field are sent to
Spark's TTS (http://127.0.0.1:8770/say) and spoken aloud.

Inbox dir: <repo>/voice_inbox/
One file per reminder, e.g. voice_inbox/1730000000.json :
{
  "content": "go to the match",          # short label (DB/logs)
  "time":    "2026-10-07 15:00",          # absolute LOCAL time, %Y-%m-%d %H:%M
  "recurring": null,                      # or "每天"/"每周一"/"工作日"/"每月1号"/"每2小时"
  "speak":   "Hey Ethan, it's 3 o'clock — time to head to the match.",  # exact words, ENGLISH
  "priority": "medium"                    # high / medium / low
}
"""
import json
import os
import shutil
import threading
import time
import urllib.request

from .config import BASE_DIR

INBOX_DIR = BASE_DIR / "voice_inbox"
PROCESSED_DIR = INBOX_DIR / "processed"
INBOX_POLL_SECONDS = 30
SAY_URL = os.environ.get("SPARK_SAY_URL", "http://127.0.0.1:8770/say")

_VALID_PRIORITY = {"high", "medium", "low"}


def ensure_dirs():
    INBOX_DIR.mkdir(exist_ok=True)
    PROCESSED_DIR.mkdir(exist_ok=True)


def speak_aloud(text: str) -> bool:
    """Send exact words to Spark's TTS (/say). Falls back to the local voice
    module (Edge TTS) if the dashboard isn't reachable."""
    text = (text or "").strip()
    if not text:
        return False
    # Preferred: the running Spark dashboard (English voice + music ducking).
    try:
        body = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            SAY_URL, data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            if r.status == 200:
                return True
    except Exception as e:
        print(f"[inbox] /say 不可达（{e}），改用本地 TTS", flush=True)
    # Fallback: Edge TTS directly (blocking).
    try:
        from .voice import speak_sync
        return bool(speak_sync(text, voice="en-US-AvaNeural"))
    except Exception as e:
        print(f"[inbox] 本地 TTS 失败：{e}", flush=True)
        return False


def _entry_to_task(entry: dict, tm):
    from .scheduler import schedule_task

    content = (entry.get("content") or "").strip()
    when = (entry.get("time") or "").strip()
    if not content or not when:
        return None
    recurring = (entry.get("recurring") or None)
    priority = entry.get("priority") if entry.get("priority") in _VALID_PRIORITY else "medium"
    speak = (entry.get("speak") or content).strip()
    task = tm.add_task(
        content=content,
        trigger_time=when,
        priority=priority,
        is_recurring=recurring,
        task_type="normal",
        meta_data={"speak": speak, "source": "inbox"},
    )
    if task and task.get("trigger_time"):
        schedule_task(task["task_id"], task["trigger_time"])
    return task


def process_inbox(tm=None) -> int:
    """Consume every JSON file in the inbox dir. Returns tasks created.

    Files that fail to parse are left in place to retry next cycle (never
    half-consumed). Successfully processed files move to processed/.
    """
    from .tasks import TaskManager

    ensure_dirs()
    tm = tm or TaskManager()
    added = 0
    now = time.time()
    for path in sorted(INBOX_DIR.glob("*.json")):
        # Skip files still being written (very fresh) — retry next cycle.
        try:
            if now - path.stat().st_mtime < 2:
                continue
        except OSError:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except Exception as e:
            print(f"[inbox] {path.name} 暂不可用（{e}），下轮重试", flush=True)
            continue
        try:
            task = _entry_to_task(data, tm)
        except Exception as e:
            print(f"[inbox] 建任务失败（{path.name}）：{e}", flush=True)
            continue
        if task is None:
            print(f"[inbox] {path.name} 缺 content/time，移入 processed 不再重试", flush=True)
        else:
            added += 1
            print(f"[inbox] 已建任务 #{task['task_id']}『{task['content']}』@ {task['trigger_time']}", flush=True)
        try:
            shutil.move(str(path), str(PROCESSED_DIR / path.name))
        except Exception as e:
            print(f"[inbox] 归档 {path.name} 失败：{e}", flush=True)
    return added


def start_inbox_watcher():
    """Poll the inbox dir forever on a background thread."""
    def _run():
        while True:
            try:
                process_inbox()
            except Exception as e:
                print(f"[inbox] 轮询出错：{e}", flush=True)
            time.sleep(INBOX_POLL_SECONDS)
    ensure_dirs()
    t = threading.Thread(target=_run, daemon=True, name="inbox-watcher")
    t.start()
    print(f"[inbox] 已启动（每 {INBOX_POLL_SECONDS} 秒检查 {INBOX_DIR}）", flush=True)
    return t
