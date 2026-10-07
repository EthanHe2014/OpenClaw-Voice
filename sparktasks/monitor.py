"""OpenMemo 多层防御/监控模块 —— 确保 AI 说到做到。

分层设计（从近到远，每层独立兜底）：
  L1 即时兑现校验：AI 回复含承诺词但本轮回合没落地任何任务 → 当场自动重试一次
  L2 落地数量校验：AI 声称建 N 个任务 → 实际入库数量核对，不符即告警
  L3 调度健康巡检：待办任务应有调度 job；缺失即补排（每 5 分钟）
  L4 语音送达校验：speak 失败重试一次；仍失败 → 记录告警 + 通知
  L5 AI 健康监控：连续调用失败 → 降级提示 + 告警

原则（沿用 Ethan 的模型）：监控只做“核对/补兜底/告警”，绝不改写 AI 的回复。
"""
import time

from .watchdog import PROMISE_KEYWORDS, USER_INTENT_KEYWORDS

# AI 连续失败计数（进程内）
_ai_failures = 0
_AI_FAILURE_ALERT_AT = 3       # 连续 3 次失败就告警
_AI_FAILURE_ALERTED = False


def note_ai_failure(ok: bool):
    """记录一次 AI 调用成败，跨过阈值时告警（L5）。"""
    global _ai_failures, _AI_FAILURE_ALERTED
    if ok:
        _ai_failures = 0
        _AI_FAILURE_ALERTED = False
        return None
    _ai_failures += 1
    if _ai_failures >= _AI_FAILURE_ALERT_AT and not _AI_FAILURE_ALERTED:
        _AI_FAILURE_ALERTED = True
        try:
            from .tasks import TaskManager
            msg = f"[AI健康] AI 连续 {_ai_failures} 次调用失败，服务可能不可用"
            TaskManager().add_alert("ai_health", msg)
        except Exception:
            pass
        return msg
    return None


def reply_promises(reply_text: str) -> bool:
    """AI 回复里是否含‘承诺’信号（已记下/会提醒/帮你设…）。"""
    if not reply_text:
        return False
    return any(k in reply_text for k in PROMISE_KEYWORDS)


def user_has_task_intent(user_text: str) -> bool:
    """用户消息是否含任务意图（提醒/记住/安排/别忘…）。"""
    if not user_text:
        return False
    return any(k in user_text for k in USER_INTENT_KEYWORDS)


def verify_landing(user_text: str, reply_text: str, action: str,
                   created_count: int, session_id: str) -> list:
    """L1+L2：核对 AI 是否真的兑现承诺。

    返回问题列表（空 = 一切正常）。
    - 承诺了但 action 非建任务类且 created=0 → 疑似‘说了没做’
    - action=task_added 但 created=0 → 想建却没建成
    """
    problems = []
    try:
        from .tasks import TaskManager
        from .conversation import _session_speaker
        tm = TaskManager()
        alert_owner = _session_speaker(session_id)   # 告警归该会话的说话人（隐私）
        if action == "task_added" and created_count == 0:
            msg = f"[兑现校验] 会话 {session_id[:10]}… AI 声称已建任务但 0 个入库：『{reply_text[:30]}』"
            problems.append(msg)
            if not tm.alert_exists(msg):
                tm.add_alert("promise", msg, owner=alert_owner)
        elif (action in ("chat", "collecting")
              and reply_promises(reply_text)
              and user_has_task_intent(user_text)
              and created_count == 0):
            msg = (f"[兑现校验] 会话 {session_id[:10]}… AI 回复『{reply_text[:30]}』"
                   f"含承诺但未建任何任务")
            problems.append(msg)
            if not tm.alert_exists(msg):
                tm.add_alert("promise", msg, owner=alert_owner)
    except Exception as e:
        print(f"[监控] verify_landing 出错：{e}")
    return problems


def reconcile_jobs():
    """L3：调度健康巡检 —— 待办任务缺 job 就补排。"""
    fixed = 0
    try:
        from datetime import datetime
        from .scheduler import scheduler, schedule_task
        from .tasks import TaskManager
        # 用新实例（动态读当前 DB_PATH），避免模块级 task_manager 绑定旧库
        task_manager = TaskManager()
        now = datetime.now()
        for t in task_manager.list_tasks(status="pending", limit=200):
            tt = t.get("trigger_time")
            if not tt:
                continue
            try:
                dt = datetime.strptime(str(tt), "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                continue
            if dt <= now:
                continue
            jid = f"task_{t['task_id']}"
            job = scheduler.get_job(jid)
            if job is None:
                schedule_task(t["task_id"], str(tt))
                fixed += 1
        if fixed:
            print(f"[监控] 调度巡检：补排 {fixed} 个缺失的提醒 job")
    except Exception as e:
        print(f"[监控] reconcile_jobs 出错：{e}")
    return fixed


def monitor_speak_ok(ok: bool, content: str, task_id: int) -> bool:
    """L4：语音送达校验 —— 调用方已重试过一次；仍失败记录告警。"""
    if ok:
        return True
    try:
        from .tasks import TaskManager
        tm = TaskManager()
        msg = f"[语音送达] 任务 #{task_id} 语音播放两次均失败（内容：{str(content)[:20]}）"
        if not tm.alert_exists(msg):
            tm.add_alert("alert", msg)
        return False
    except Exception as e:
        print(f"[监控] monitor_speak_ok 出错：{e}")
        return False
