"""Spark 任务调度器 —— 纯调度，无 AI。

从 OpenMemo 移植并剥离全部 AI 依赖。任务由 AI 通过 voice_inbox.jsonl 交进来
（见 inbox.py），到点后直接把任务里存的 `speak` 原文用 Spark 的 TTS 念出来
（英文，原文，不改写）。
"""
import asyncio
import re
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from .tasks import TaskManager

scheduler = AsyncIOScheduler()
task_manager = TaskManager()


def _finish_reminder(task: dict):
    """提醒触发收尾：标记已提醒；一次性任务 → completed，循环任务保持 pending。"""
    task_id = task["task_id"]
    task_manager.mark_reminded(task_id)
    if not task.get("is_recurring"):
        task_manager.update_task(task_id, status="completed")


async def reminder_callback(task_id: int):
    """提醒触发：把任务里存的原文（英文）念出来。无 AI。"""
    task = task_manager.get_task(task_id)
    if not task or task["status"] != "pending":
        return

    # 先标记已提醒，避免巡检把执行中的任务误判为"过期未触发"
    task_manager.mark_reminded(task_id)

    content = task["content"]
    meta = task.get("meta_data") or {}
    speech = (meta.get("speak") or content or "").strip()

    print(f"[提醒] 播报：{speech}", flush=True)

    from .inbox import speak_aloud
    ok = speak_aloud(speech)
    if not ok:
        print("[提醒] 首次播报失败，重试一次", flush=True)
        speak_aloud(speech)

    # 记录提醒原文（供 /api/reminders 或日志查看）
    task_manager.add_reminder(task_id, content, speech, owner=task.get("owner"))

    _finish_reminder(task)
    # 循环任务：安排下一次
    if task["is_recurring"]:
        schedule_recurring(task_id, task["is_recurring"], content, task["priority"])


def schedule_recurring(task_id: int, recurring: str, content: str, priority: str):
    """安排循环任务的下一次（复用同一 task_id）。"""
    task = task_manager.get_task(task_id)
    if not task:
        return
    meta = task.get("meta_data") or {}

    next_dt = _next_occurrence(task, recurring)
    if next_dt is None:
        task_manager.update_task(task_id, status="completed")
        return

    period = meta.get("period")
    if period and period not in ("长期", "", None):
        try:
            deadline = datetime.strptime(str(period)[:10], "%Y-%m-%d")
            if next_dt.date() > deadline:
                print(f"[调度器] 任务 {task_id} 已过执行周期（{period}），完结", flush=True)
                task_manager.update_task(task_id, status="completed")
                return
        except (ValueError, TypeError):
            pass

    next_str = next_dt.strftime("%Y-%m-%d %H:%M")
    task_manager.update_task(task_id, trigger_time=next_str, reminder_sent=0, status="pending")
    schedule_task(task_id, next_str)
    print(f"[调度器] 循环任务 {task_id} 下一次：{next_str}", flush=True)


def _next_occurrence(task: dict, recurring: str):
    """根据循环模式计算下一次触发时间（保留任务原定的时分）。None 表示无法计算。"""
    from datetime import timedelta
    import calendar
    recurring_lower = (recurring or "").lower()
    now = datetime.now()

    hour = minute = 0
    base = None
    tt = task.get("trigger_time")
    if tt:
        try:
            base = datetime.strptime(str(tt), "%Y-%m-%d %H:%M")
            hour, minute = base.hour, base.minute
        except (ValueError, TypeError):
            pass

    def at(y, mo, d):
        return datetime(y, mo, d, hour, minute)

    if recurring_lower in ("每天", "daily"):
        return at(now.year, now.month, now.day) + timedelta(days=1)
    if "工作日" in recurring_lower or "weekday" in recurring_lower:
        nxt = at(now.year, now.month, now.day) + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt

    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    m = re.search(r"每周([一二三四五六日天、，和及]+)", recurring_lower)
    if m:
        days = [weekday_map[ch] for ch in m.group(1) if ch in weekday_map]
        if days:
            nxt = at(now.year, now.month, now.day) + timedelta(days=1)
            while nxt.weekday() not in days:
                nxt += timedelta(days=1)
            return nxt

    m = re.search(r"每(\d+)\s*(小时|分钟|天)", recurring_lower)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "小时":
            return now + timedelta(hours=n)
        if unit == "分钟":
            return now + timedelta(minutes=n)
        if unit == "天":
            return at(now.year, now.month, now.day) + timedelta(days=n)

    if "每月" in recurring_lower:
        m = re.search(r"每月(\d{1,2})[号日]", recurring_lower)
        if m:
            day = int(m.group(1))
        else:
            day = base.day if base else now.day
        year, month = now.year, now.month
        for _ in range(13):
            month += 1
            if month > 12:
                month = 1
                year += 1
            last_day = calendar.monthrange(year, month)[1]
            nxt = at(year, month, min(day, last_day))
            if nxt > now:
                return nxt
        return None
    return None


def schedule_task(task_id: int, trigger_time: str) -> bool:
    """安排任务提醒。True=已调度，False=时间格式无效。
    时间已过（含刚过）→ 立即触发，绝不静默丢弃。"""
    try:
        dt = datetime.strptime(str(trigger_time), "%Y-%m-%d %H:%M")
    except ValueError as e:
        print(f"[调度器] 任务 {task_id} 时间格式无效：{e}", flush=True)
        return False
    now = datetime.now()
    if dt <= now:
        # 时间已到/已过：立即触发（不能让它变成永不触发的僵尸任务）
        print(f"[调度器] 任务 {task_id} 时间已过，立即触发", flush=True)
        scheduler.add_job(
            reminder_callback,
            args=[task_id],
            id=f"task_{task_id}_overdue",
            replace_existing=True,
        )
        return True
    scheduler.add_job(
        reminder_callback,
        trigger=DateTrigger(run_date=dt),
        args=[task_id],
        id=f"task_{task_id}",
        replace_existing=True,
    )
    print(f"[调度器] 已安排任务 {task_id}，提醒时间：{trigger_time}", flush=True)
    return True


def load_existing_tasks():
    """启动时加载并安排所有待办任务。"""
    tasks = task_manager.list_tasks(status="pending")
    now = datetime.now()
    for task in tasks:
        tt = task.get("trigger_time")
        if not tt:
            continue
        try:
            dt = datetime.strptime(str(tt), "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            print(f"[调度器] 任务 {task['task_id']} 时间格式无效：{tt}", flush=True)
            continue
        if task.get("reminder_sent"):
            continue
        if dt > now:
            schedule_task(task["task_id"], str(tt))
        else:
            print(f"[调度器] 任务 {task['task_id']} 已过期，立即触发", flush=True)
            scheduler.add_job(
                reminder_callback,
                args=[task["task_id"]],
                id=f"task_{task['task_id']}_overdue",
                replace_existing=True,
            )


def reconcile_jobs() -> int:
    """巡检：待办任务缺 job 就补排；已过时间但未触发的，立即补触发。"""
    fixed = 0
    now = datetime.now()
    for t in task_manager.list_tasks(status="pending", limit=200):
        tt = t.get("trigger_time")
        if not tt:
            continue
        try:
            dt = datetime.strptime(str(tt), "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            continue
        has_job = (scheduler.get_job(f"task_{t['task_id']}") is not None
                   or scheduler.get_job(f"task_{t['task_id']}_overdue") is not None)
        if dt > now:
            if not has_job:
                schedule_task(t["task_id"], str(tt))
                fixed += 1
        else:
            # 时间已过但还没触发 → 立即补触发（防僵尸任务静默失败）
            if not has_job and not t.get("reminder_sent"):
                schedule_task(t["task_id"], str(tt))
                fixed += 1
    if fixed:
        print(f"[调度器] 巡检补排 {fixed} 个提醒 job", flush=True)
    return fixed


def _json_safe_add(job_fn, trigger, **kw):
    scheduler.add_job(job_fn, trigger, **kw)


def start_scheduler():
    """启动调度器 + 收件箱轮询 + 巡检 + 清理。"""
    load_existing_tasks()
    scheduler.start()
    print("[调度器] 已启动", flush=True)

    from .inbox import start_inbox_watcher
    start_inbox_watcher()

    from apscheduler.triggers.interval import IntervalTrigger

    # 巡检：每 5 分钟补排缺失的 job
    def reconcile_tick():
        try:
            reconcile_jobs()
        except Exception as e:
            print(f"[调度器] 巡检出错：{e}", flush=True)

    reconcile_tick()
    _json_safe_add(reconcile_tick, IntervalTrigger(minutes=5),
                   id="reconcile_jobs", replace_existing=True, max_instances=1)

    # 清理：每 6 小时清理已完结超 24h / 软删除超 7 天的任务
    def cleanup_tick():
        try:
            task_manager.delete_finished_old(older_than_hours=24)
            task_manager.purge_deleted_old(older_than_hours=168)
        except Exception as e:
            print(f"[清理] 出错：{e}", flush=True)

    cleanup_tick()
    _json_safe_add(cleanup_tick, IntervalTrigger(hours=6),
                   id="cleanup_finished_tasks", replace_existing=True, max_instances=1)

    # L1/L2 盘点：每 60 秒核对 用户输入 vs 任务库 vs 待处理收件箱
    def monitor_tick():
        try:
            from .monitor import run_checks
            run_checks()
        except Exception as e:
            print(f"[监控] 出错：{e}", flush=True)

    _json_safe_add(monitor_tick, IntervalTrigger(seconds=60),
                   id="landing_checks", replace_existing=True, max_instances=1)
    print("[调度器] 巡检(5m) + 清理(6h) + L1/L2盘点(60s) 已启动", flush=True)


def stop_scheduler():
    """停止调度器。"""
    try:
        scheduler.shutdown()
    except Exception:
        pass
    print("[调度器] 已停止", flush=True)
