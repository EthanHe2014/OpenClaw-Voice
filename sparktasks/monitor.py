"""L1/L2 landing checks — deterministic, no AI, no reply analysis.

Continuously compares three things:
  - USER INPUT   : voice_turns.jsonl (written by spark_web on each utterance)
  - JOBS DB      : the tasks table (created_at is the source of truth)
  - WAITING LIST : files sitting in voice_inbox/ not yet consumed

L1 (landing): a user turn that carries a task intent must result in either a task
row created in the window, or an inbox file waiting in the window. If neither,
the intent was dropped → alert.

L2 (armed): a pending task whose trigger time is still in the future must have a
live scheduler job. If reconcile could not arm it, alert.

All state is timestamps (created_at / mtime), never text matching of the reply.
"""
import json
import time
from datetime import datetime

from .config import BASE_DIR
from .tasks import TaskManager

TURNS_FILE = BASE_DIR / "voice_turns.jsonl"
INBOX_DIR = BASE_DIR / "voice_inbox"
STATE_FILE = BASE_DIR / "voice_landing.state.json"
ALERT_LOG = BASE_DIR / "voice_alerts.log"

# A turn is only judged once this many seconds have passed, so late writes land.
SETTLE_SECONDS = 200          # covered by agent + 30s watcher + margin
EARLY_GRACE = 20              # task may be created moments before the turn timestamp
TURN_SCAN_SECONDS = 3600      # only look at the last hour of turns

# Deterministic task-intent keywords (no AI; mirrors the agent's own triggers).
INTENT_KEYWORDS = [
    "提醒", "记住", "记下", "记一下", "安排", "别忘", "到点", "几点", "叫我",
    "每天", "每周", "每月", "帮我记", "帮我设", "定时", "闹钟", "日程",
    "remind", "reminder", "remember", "schedule", "don't forget", "dont forget",
    "at ", "o'clock", "oclock", "tomorrow", "tonight", "meeting", "match",
]


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"last_turn_ts": 0.0}


def _save_state(st: dict):
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception:
        pass


def _alert(kind: str, message: str):
    print(f"[监控:{kind}] {message}", flush=True)
    try:
        tm = TaskManager()
        if not tm.alert_exists(message):
            tm.add_alert(kind, message)
    except Exception as e:
        print(f"[监控] 告警入库失败：{e}", flush=True)
    try:
        with open(ALERT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t[{kind}] {message}\n")
    except Exception:
        pass


def _has_intent(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in INTENT_KEYWORDS)


def _read_turns() -> list:
    if not TURNS_FILE.exists():
        return []
    out = []
    try:
        for line in TURNS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                if isinstance(o, dict) and "t" in o:
                    out.append(o)
            except Exception:
                continue
    except Exception:
        return []
    return out


def _inbox_times() -> list:
    """Creation times (epoch) of files still waiting in voice_inbox/."""
    if not INBOX_DIR.exists():
        return []
    out = []
    for p in INBOX_DIR.glob("*.json"):
        try:
            out.append(p.stat().st_mtime)
        except OSError:
            continue
    return out


def _tasks_created_between(tm: TaskManager, a: float, b: float) -> list:
    """Task rows whose created_at falls in [a, b]."""
    res = []
    lo = datetime.fromtimestamp(a).strftime("%Y-%m-%d %H:%M:%S")
    hi = datetime.fromtimestamp(b).strftime("%Y-%m-%d %H:%M:%S")
    for t in tm.list_tasks(status=None, limit=500, include_deleted=True):
        ca = t.get("created_at")
        if ca and lo <= str(ca) <= hi:
            res.append(t)
    return res


def check_landing(tm: TaskManager = None) -> list:
    """L1: every settled task-intent turn must have landed (job row OR inbox file).
    Returns the list of problem messages."""
    tm = tm or TaskManager()
    st = _load_state()
    last_ts = float(st.get("last_turn_ts") or 0.0)
    now = time.time()
    problems = []
    newest_settled = last_ts

    turns = _read_turns()
    for o in turns:
        try:
            ts = float(o["t"])
        except (KeyError, ValueError, TypeError):
            continue
        if ts <= last_ts:
            continue                      # already judged
        if now - ts < SETTLE_SECONDS:
            continue                      # too fresh — wait for it to settle
        if now - ts > TURN_SCAN_SECONDS:
            newest_settled = max(newest_settled, ts)   # too old to judge now
            continue
        newest_settled = max(newest_settled, ts)
        text = o.get("text", "")
        if not _has_intent(text):
            continue
        win_lo = ts - EARLY_GRACE
        win_hi = ts + SETTLE_SECONDS
        landed_tasks = _tasks_created_between(tm, win_lo, win_hi)
        inbox_hit = any(win_lo <= m <= win_hi for m in _inbox_times())
        if not landed_tasks and not inbox_hit:
            iso = o.get("iso") or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
            msg = (f"[L1 未落地] {iso} 用户『{text[:36]}』无任务入库、无收件箱文件"
                   f"（{SETTLE_SECONDS}s 窗口）")
            problems.append(msg)
            _alert("landing", msg)

    if newest_settled > last_ts:
        st["last_turn_ts"] = newest_settled
        _save_state(st)
    return problems


def check_armed(tm: TaskManager = None) -> list:
    """L2: pending future tasks must have a live scheduler job."""
    from .scheduler import scheduler
    tm = tm or TaskManager()
    problems = []
    now = datetime.now()
    for t in tm.list_tasks(status="pending", limit=500):
        tt = t.get("trigger_time")
        if not tt:
            continue
        try:
            dt = datetime.strptime(str(tt), "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            continue
        if dt <= now:
            continue                      # overdue path, handled by reconcile
        if scheduler.get_job(f"task_{t['task_id']}") is None:
            msg = f"[L2 未挂载] 任务 #{t['task_id']}『{str(t['content'])[:24]}』@{tt} 无调度 job"
            problems.append(msg)
            _alert("armed", msg)
    return problems


def run_checks() -> list:
    """Run L1+L2 once. Returns all problems found."""
    tm = TaskManager()
    return check_landing(tm) + check_armed(tm)
