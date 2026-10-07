"""对话历史 vs 任务库 一致性看护（Watchdog）

职责：定期扫描最近的对话历史，与任务数据库对照，找出三类问题：
1. AI 承诺了但没建任务（"好的，已记下" 但库里没有对应任务）
2. 任务时间在过去（永远不会触发，静默失败）
3. 疑似重复任务（同一内容+时间短时间内建了多次）

纯诊断/监控工具，不做任何对话决策，不干预 AI 行为。
"""
import sqlite3
from datetime import datetime, timedelta

from .config import DB_PATH
from .tasks import TaskManager

# AI 回复里的"承诺"信号
PROMISE_KEYWORDS = [
    "提醒你", "已记下", "记下了", "记好了", "安排好了", "已安排",
    "设置了", "会提醒", "帮你记", "到点", "准时报", "建立", "帮你设",
]
# 用户消息里的"任务意图"信号
USER_INTENT_KEYWORDS = [
    "提醒", "记住", "记下", "安排", "别忘", "到点", "几点",
    "每天", "每周", "每月", "帮我记", "播报", "新闻", "天气",
]


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _is_followup_question(reply_text: str) -> bool:
    """AI 回复若是追问/确认问句（还没落地），不算'承诺未落地'。
    启发式：含问号、或以常见追问词结尾/开头。"""
    if not reply_text:
        return False
    t = reply_text.strip()
    if any(q in t for q in ("？", "?")):
        return True
    # 无问号但明显在问（口语省略问号的情况较少，保守处理）
    endings = ("几点", "什么时候", "可以吗", "好吗", "行吗", "怎么样", "如何", "要不要", "对吧")
    return t.endswith(endings) or any(t.endswith(e) for e in endings)


def _load_conversations(since: datetime) -> list:
    """按会话顺序取最近对话（user/assistant 成对）。"""
    conn = _db()
    cur = conn.cursor()
    rows = cur.execute(
        """SELECT session_id, role, content, created_at FROM conversations
           WHERE created_at >= ? ORDER BY session_id, rowid""",
        (since.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _tasks_created_in_window(start: datetime, end: datetime, session_id: str = None) -> list:
    conn = _db()
    cur = conn.cursor()
    if session_id:
        # 只认该会话建的任务（meta_data 里存了 session_id），
        # 避免同分钟别的会话建了任务导致"看起来落地了"的漏报。
        rows = cur.execute(
            """SELECT task_id, content, trigger_time, status, created_at, meta_data FROM tasks
               WHERE created_at >= ? AND created_at <= ? AND deleted_at IS NULL AND meta_data LIKE ?""",
            (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"),
             f"%\"session_id\": \"{session_id}\"%"),
        ).fetchall()
    else:
        rows = cur.execute(
            """SELECT task_id, content, trigger_time, status, created_at FROM tasks
               WHERE created_at >= ? AND created_at <= ? AND deleted_at IS NULL""",
            (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _all_tasks() -> list:
    conn = _db()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT task_id, content, trigger_time, status, reminder_sent, created_at, is_recurring FROM tasks WHERE deleted_at IS NULL"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def run_watchdog(hours: int = 24, auto_fix: bool = True) -> list:
    """执行一次检查，返回问题列表（每条是一段可读的中文描述）。

    auto_fix=True（默认）时：
    - 疑似重复 → 自动删除后建的那个任务（保留先建的），并记录告警
    - 过期时间 → 自动取消（status=cancelled），并记录告警
    - 承诺未落地 → 无法自动修复，仅记录告警
    每条告警只报一次（24h 去重），避免每个 tick 重复刷屏。
    """
    problems = []
    tm = TaskManager()
    now = datetime.now()
    since = now - timedelta(hours=hours)
    conversations = _load_conversations(since)

    # ── 1. 承诺 vs 落地 ──────────────────────────────────────
    # 按会话分组，遍历 user → assistant 配对
    by_session: dict = {}
    for m in conversations:
        by_session.setdefault(m["session_id"], []).append(m)

    for sid, msgs in by_session.items():
        for i, m in enumerate(msgs):
            if m["role"] != "user":
                continue
            # 找紧随其后的 assistant 回复
            reply = None
            for nxt in msgs[i + 1:]:
                if nxt["role"] == "assistant":
                    reply = nxt
                    break
            if not reply:
                continue
            user_text = m["content"] or ""
            reply_text = reply["content"] or ""
            # AI 正在追问补全信息（回复是问句）→ 不算"承诺未落地"，跳过
            if _is_followup_question(reply_text):
                continue
            # 用户有任务意图 + AI 回复里有承诺 → 必须在窗口内落地任务
            if not any(k in user_text for k in USER_INTENT_KEYWORDS):
                continue
            if not any(k in reply_text for k in PROMISE_KEYWORDS):
                continue
            try:
                user_dt = datetime.strptime(m["created_at"], "%Y-%m-%d %H:%M:%S")
                reply_dt = datetime.strptime(reply["created_at"], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
            window_start = user_dt - timedelta(minutes=1)
            # ⚠️ 宽松到 3 分钟：AI 常在回复后一小会儿才落地任务（L1 补建/多轮），
            # 1 分钟窗口会产生"任务其实建了"的假告警，然后每 60s 重复弹通知
            window_end = reply_dt + timedelta(minutes=3)
            # 按会话核对落地：同分钟别家会话建的任务不算数
            landed = _tasks_created_in_window(window_start, window_end, session_id=sid)
            # 任务可能已落地但随后被用户删除/自动清理：删除留痕里能匹配到
            # 用户原话 → 承诺兑现过，不算"承诺未落地"。
            # ⚠️ 删除可能发生在承诺之后很久（用户手动清理），窗口要放宽到「现在」
            if not landed and tm.task_deleted_in_window(user_text, window_start, datetime.now()):
                continue
            if not landed:
                msg = (
                    f"[承诺未落地] 会话 {sid[:12]}… {user_dt.strftime('%H:%M')} "
                    f"用户:『{user_text[:24]}』→ AI 回复『{reply_text[:24]}』但 1 分钟内无任务入库"
                )
                problems.append(msg)
                if auto_fix and not tm.alert_exists(msg):
                    tm.add_alert("promise", msg)

    # ── 2. 时间在过去、永远不会触发的任务（近 24h 新建）────
    for t in _all_tasks():
        try:
            created = datetime.strptime(t["created_at"], "%Y-%m-%d %H:%M:%S")
            trig = datetime.strptime(t["trigger_time"], "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            continue
        if created < since:
            continue
        if t["status"] == "pending" and trig < now and not t["reminder_sent"]:
            # 循环任务：时间过了不该取消——立即补触发一次（触发后自动重排下一次）
            if t.get("is_recurring"):
                from .scheduler import scheduler as sched, reminder_callback as cb
                try:
                    sched.add_job(cb, args=[t["task_id"]], id=f"task_{t['task_id']}_overdue", replace_existing=True)
                    fix_msg = (
                        f"[自动处理] 循环任务 #{t['task_id']}『{t['content'][:20]}』"
                        f"已过触发时间，立即补触发"
                    )
                except Exception as e:
                    fix_msg = f"[自动处理] 循环任务 #{t['task_id']} 补触发失败：{e}"
                print(f"[看护] {fix_msg}")
                if auto_fix and not tm.alert_exists(fix_msg):
                    tm.add_alert("autofix", fix_msg)
                continue
            msg = (
                f"[过期时间] 任务 #{t['task_id']}『{t['content'][:20]}』"
                f"触发时间 {t['trigger_time']} 已过去，永远不会触发"
            )
            problems.append(msg)
            if auto_fix:
                # 自动处理：取消这个永远触发不了的一次性任务
                tm.update_task(t["task_id"], status="cancelled")
                fix_msg = f"[自动处理] 已取消过期任务 #{t['task_id']}『{t['content'][:20]}』（时间已过）"
                print(f"[看护] {fix_msg}")
                if not tm.alert_exists(fix_msg):
                    tm.add_alert("autofix", fix_msg)

    # ── 3. 疑似重复（近 24h 新建，同内容+同时间 3 分钟内）──
    # ⚠️ 只对比同一 owner 的任务：两个人各自建“买牛奶”不是重复，不能删别人的
    recent = [t for t in _all_tasks() if t["created_at"] and t["created_at"] >= since.strftime("%Y-%m-%d %H:%M:%S")]
    for i, a in enumerate(recent):
        for b in recent[i + 1:]:
            if a.get("owner") != b.get("owner"):
                continue
            if a["content"] == b["content"] and a["trigger_time"] and a["trigger_time"] == b["trigger_time"]:
                try:
                    da = datetime.strptime(a["created_at"], "%Y-%m-%d %H:%M:%S")
                    db = datetime.strptime(b["created_at"], "%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError):
                    continue
                if abs((da - db).total_seconds()) <= 180:
                    msg = (
                        f"[疑似重复] 任务 #{a['task_id']} 与 #{b['task_id']} "
                        f"内容『{a['content'][:20]}』时间 {a['trigger_time']} 重复"
                    )
                    problems.append(msg)
                    if auto_fix:
                        # 自动处理：删掉后建的那个（b），保留先建的（a）
                        keep_id = a["task_id"] if da <= db else b["task_id"]
                        drop_id = b["task_id"] if keep_id == a["task_id"] else a["task_id"]
                        tm.delete_task(drop_id)
                        fix_msg = (
                            f"[自动处理] 已删除重复任务 #{drop_id}『{a['content'][:20]}』"
                            f"（与 #{keep_id} 重复，保留先建的）"
                        )
                        print(f"[看护] {fix_msg}")
                        if not tm.alert_exists(fix_msg):
                            tm.add_alert("autofix", fix_msg)
                    break  # 一组报一次即可

    return problems


def main():
    """CLI：python -m openmemo.watchdog --once"""
    import sys
    hours = 24
    if "--hours" in sys.argv:
        try:
            hours = int(sys.argv[sys.argv.index("--hours") + 1])
        except (ValueError, IndexError):
            pass
    problems = run_watchdog(hours=hours)
    if not problems:
        print("✅ 无问题：对话与任务库一致")
    else:
        print(f"⚠️ 发现 {len(problems)} 个问题：")
        for p in problems:
            print(" -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
