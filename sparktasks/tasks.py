"""基于 SQLite 的任务管理"""
import sqlite3
import json
from datetime import datetime
from typing import Optional, List
from .config import DB_PATH


def get_db() -> sqlite3.Connection:
    """Get a database connection with row factory"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database schema"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        task_id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        trigger_time TEXT,
        duration_minutes INTEGER DEFAULT NULL,
        priority TEXT DEFAULT 'medium',
        status TEXT DEFAULT 'pending',
        is_recurring TEXT DEFAULT NULL,
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        updated_at TEXT DEFAULT (datetime('now', 'localtime')),
        reminder_sent INTEGER DEFAULT 0,
        notes TEXT DEFAULT NULL,
        owner TEXT DEFAULT NULL
    )
    """)

    # Migration: add owner column (说话人归属)
    try:
        cursor.execute("ALTER TABLE tasks ADD COLUMN owner TEXT DEFAULT NULL")
        conn.commit()
        print("Migrated: added owner column")
    except sqlite3.OperationalError:
        pass

    # Migration: add duration_minutes column if it doesn't exist (existing DBs)
    try:
        cursor.execute("ALTER TABLE tasks ADD COLUMN duration_minutes INTEGER DEFAULT NULL")
        conn.commit()
        print("Migrated: added duration_minutes column")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        cursor.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT 'normal'")
        conn.commit()
        print("Migrated: added task_type column")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE tasks ADD COLUMN meta_data TEXT DEFAULT NULL")
        conn.commit()
        print("Migrated: added meta_data column")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: add deleted_at column (软删除/可恢复) if it doesn't exist
    try:
        cursor.execute("ALTER TABLE tasks ADD COLUMN deleted_at TEXT DEFAULT NULL")
        conn.commit()
        print("Migrated: added deleted_at column")
    except sqlite3.OperationalError:
        pass  # Column already exists

    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_titles (
        session_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
        conv_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        intent TEXT DEFAULT NULL,
        slots TEXT DEFAULT NULL,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)

    # Migration: 消息情绪 / 声音事件 / 说话人 独立列（旧库补列）
    for col in ("emotion", "event", "speaker"):
        try:
            cursor.execute(f"ALTER TABLE conversations ADD COLUMN {col} TEXT DEFAULT NULL")
            conn.commit()
            print(f"Migrated: added conversations.{col} column")
        except sqlite3.OperationalError:
            pass  # Column already exists
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pending_slots (
        pending_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        intent TEXT DEFAULT 'ADD_TASK',
        partial_slots TEXT NOT NULL,
        missing_slots TEXT NOT NULL,
        original_message TEXT,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)
    
    # Migration: add intent column to pending_slots if missing (existing DBs)
    try:
        cursor.execute("ALTER TABLE pending_slots ADD COLUMN intent TEXT DEFAULT 'ADD_TASK'")
        conn.commit()
        print("Migrated: added intent column to pending_slots")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # 迁移：旧版 executed 状态统一为 completed（只保留 待办/已完成/已取消 三种）
    try:
        cursor.execute("UPDATE tasks SET status='completed' WHERE status='executed'")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    # 看护告警：watchdog 发现的问题（自动处理后也留痕），App 轮询后显示
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS alerts (
        alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        message TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)

    # 提醒送达记录：每次提醒触发时，把 AI 生成的提醒原文存下来，App 轮询后显示
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER,
        content TEXT,
        message TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)

    # 任务删除留痕：看护判断“承诺是否落地”时，任务被删 ≠ 承诺未兑现
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS task_deletions (
        deletion_id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER,
        content TEXT,
        deleted_by TEXT DEFAULT 'user',
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
    """)

    # 隐私：提醒/告警归说话人（谁的任务提醒、谁的会话告警 → 只给 TA 看）
    try:
        cursor.execute("ALTER TABLE reminders ADD COLUMN owner TEXT DEFAULT NULL")
        print("Migrated: added owner column to reminders")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE alerts ADD COLUMN owner TEXT DEFAULT NULL")
        print("Migrated: added owner column to alerts")
    except sqlite3.OperationalError:
        pass
    conn.commit()

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


class TaskManager:
    """Manages tasks in SQLite"""
    
    def __init__(self):
        init_db()
    
    def add_task(self, content: str, trigger_time: str = None, 
                 priority: str = "medium", is_recurring: str = None,
                 notes: str = None, duration_minutes: int = None,
                 task_type: str = "normal", meta_data: dict = None,
                 owner: str = None) -> dict:
        """Add a new task. task_type: normal/news/travel/schedule. meta_data: intent-specific JSON.
        owner: 说话人（谁创建的，任务只给 TA 看）。"""
        conn = get_db()
        cursor = conn.cursor()
        
        meta_json = json.dumps(meta_data, ensure_ascii=False) if meta_data else None
        cursor.execute("""
        INSERT INTO tasks (content, trigger_time, priority, is_recurring, notes, duration_minutes,
                           task_type, meta_data, owner)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (content, trigger_time, priority, is_recurring, notes, duration_minutes,
              task_type, meta_json, owner))
        
        task_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return self.get_task(task_id)
    
    def get_task(self, task_id: int) -> Optional[dict]:
        """Get a task by ID"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            d = dict(row)
            if d.get("meta_data"):
                try:
                    d["meta_data"] = json.loads(d["meta_data"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return d
        return None
    
    def list_tasks(self, status: str = None, limit: int = 20, include_deleted: bool = False,
                    owner: str = None) -> List[dict]:
        """List tasks, optionally filtered by status（默认排除软删除）。
        owner 指定时：只返回 TA 的任务（owner IS NULL 的未认领任务不给任何人看）。"""
        conn = get_db()
        cursor = conn.cursor()
        
        deleted_filter = "" if include_deleted else " AND deleted_at IS NULL"
        owner_filter = ""
        args = []
        # ⚠️ 占位符顺序必须与 SQL 一致：status 在 WHERE 前面，owner 在后面
        if status:
            args.append(status)
            where = "status = ?"
        else:
            where = "1=1"
        if owner:
            # 隐私：指定 owner 时只返回 TA 的任务。owner IS NULL 的任务是
            # 未认领数据，不给任何人看（否则任何识别出的人都能看到）。
            owner_filter = " AND owner = ?"
            args.append(owner)
        cursor.execute(
            f"""SELECT * FROM tasks WHERE {where} {deleted_filter}{owner_filter}
               ORDER BY 
                 CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 2 END,
                 trigger_time ASC LIMIT ?""",
            (*args, limit)
        )
        
        rows = cursor.fetchall()
        conn.close()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("meta_data"):
                try:
                    d["meta_data"] = json.loads(d["meta_data"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(d)
        return result
    
    def update_task(self, task_id: int, **kwargs) -> Optional[dict]:
        """Update a task's fields"""
        allowed_fields = ['content', 'trigger_time', 'priority', 'status', 
                         'is_recurring', 'notes', 'reminder_sent', 'duration_minutes']
        
        updates = []
        values = []
        for field, value in kwargs.items():
            if field in allowed_fields:
                updates.append(f"{field} = ?")
                values.append(value)
        
        if not updates:
            return self.get_task(task_id)
        
        updates.append("updated_at = datetime('now', 'localtime')")
        values.append(task_id)
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?", values)
        conn.commit()
        conn.close()
        
        return self.get_task(task_id)
    
    def complete_task(self, task_id: int) -> Optional[dict]:
        """Mark a task as completed"""
        return self.update_task(task_id, status="completed")
    
    def cancel_task(self, task_id: int) -> Optional[dict]:
        """Cancel a task"""
        return self.update_task(task_id, status="cancelled")
    
    def delete_task(self, task_id: int) -> bool:
        """软删除任务（可恢复）：标记 deleted_at，保留数据。
        删除留痕供看护判断承诺落地。"""
        conn = get_db()
        cursor = conn.cursor()
        # 先留痕：记录被删任务的内容，供 watchdog 区分"已落地后被删"与"从未落地"
        row = cursor.execute(
            "SELECT content FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row:
            cursor.execute(
                "INSERT INTO task_deletions (task_id, content, deleted_by) VALUES (?, ?, 'user')",
                (task_id, row["content"]),
            )
        cursor.execute(
            "UPDATE tasks SET deleted_at = datetime('now','localtime'), "
            "updated_at = datetime('now','localtime') WHERE task_id = ?",
            (task_id,),
        )
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def restore_task(self, task_id: int) -> Optional[dict]:
        """恢复被软删除的任务：清除 deleted_at，状态回到待办。"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tasks SET deleted_at = NULL, status = 'pending', "
            "updated_at = datetime('now','localtime') WHERE task_id = ? AND deleted_at IS NOT NULL",
            (task_id,),
        )
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        if not affected:
            return None
        task = self.get_task(task_id)
        # 恢复后若还有未来时间，重新调度提醒
        if task and task.get("trigger_time"):
            try:
                from .scheduler import schedule_task
                schedule_task(task_id, task["trigger_time"])
            except Exception:
                pass
        return task

    def list_deleted_tasks(self, limit: int = 50) -> List[dict]:
        """列出被软删除的任务（用于恢复）。"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM tasks WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("meta_data"):
                try:
                    d["meta_data"] = json.loads(d["meta_data"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(d)
        return result

    def purge_deleted_old(self, older_than_hours: int = 168) -> int:
        """硬删除超过 N 小时的软删除任务（默认 7 天）。返回删除条数。"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM tasks WHERE deleted_at IS NOT NULL "
            "AND deleted_at < datetime('now','localtime', ?)",
            (f"-{int(older_than_hours)} hours",),
        )
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        if affected:
            print(f"[清理] 硬删除 {affected} 个超过 {older_than_hours}h 的软删除任务")
        return affected

    def task_deleted_in_window(self, content_fragment: str, start, end) -> bool:
        """指定时间窗口内，是否删除过与 content_fragment 相关的任务。
        双向匹配：删除的任务内容包含用户原话片段，或用户原话包含任务内容。
        用于看护：AI 承诺过且任务确实建过，但后来被用户删了 → 不算承诺未落地。"""
        conn = get_db()
        cursor = conn.cursor()
        rows = cursor.execute(
            """SELECT content FROM task_deletions
               WHERE created_at >= ? AND created_at <= ?""",
            (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()
        conn.close()
        fragment = (content_fragment or "").strip()
        if not fragment:
            return False
        for r in rows:
            deleted_content = (r["content"] or "").strip()
            if not deleted_content:
                continue
            # 任务内容包含原话（去超市买东西 ⊂ 提醒我明天去超市买东西）
            if deleted_content in fragment:
                return True
            # 原话包含任务内容（提醒我买牛奶 ⊃ 买牛奶）
            if fragment in deleted_content:
                return True
        return False

    def delete_finished_old(self, older_than_hours: int = 24) -> int:
        """清理已完结（completed/cancelled）且超过 N 小时的任务。
        返回删除条数。待办（pending）任务绝不动。"""
        conn = get_db()
        cursor = conn.cursor()
        # 先留痕（自动清理删除，供看护判断）
        rows = cursor.execute(
            """SELECT task_id, content FROM tasks
               WHERE status IN ('completed', 'cancelled')
                 AND updated_at < datetime('now', 'localtime', ?)""",
            (f"-{int(older_than_hours)} hours",),
        ).fetchall()
        for r in rows:
            cursor.execute(
                "INSERT INTO task_deletions (task_id, content, deleted_by) VALUES (?, ?, 'cleanup')",
                (r["task_id"], r["content"]),
            )
        cursor.execute(
            """DELETE FROM tasks
               WHERE status IN ('completed', 'cancelled')
                 AND updated_at < datetime('now', 'localtime', ?)""",
            (f"-{int(older_than_hours)} hours",),
        )
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        if affected:
            print(f"[清理] 删除 {affected} 个已完结超 {older_than_hours}h 的任务")
        return affected
    
    def get_pending_reminders(self) -> List[dict]:
        """Get tasks that need reminders (trigger_time <= now, not yet reminded)"""
        conn = get_db()
        cursor = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute("""
            SELECT * FROM tasks 
            WHERE status = 'pending' 
            AND trigger_time <= ? 
            AND reminder_sent = 0
            ORDER BY priority DESC
        """, (now,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def mark_reminded(self, task_id: int):
        """Mark a task as reminded"""
        self.update_task(task_id, reminder_sent=1)
    
    def get_overdue_tasks(self) -> List[dict]:
        """Get tasks that are past their trigger time and still pending"""
        conn = get_db()
        cursor = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute("""
            SELECT * FROM tasks 
            WHERE status = 'pending' 
            AND trigger_time < ? 
            AND reminder_sent = 1
            ORDER BY priority DESC
        """, (now,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def search_tasks(self, query: str, include_deleted: bool = False, owner: str = None) -> List[dict]:
        """Search tasks by content（默认排除软删除）。owner 指定时只搜 TA 的任务。"""
        conn = get_db()
        cursor = conn.cursor()
        deleted_filter = "" if include_deleted else " AND deleted_at IS NULL"
        owner_filter = " AND owner = ?" if owner else ""
        args = (f"%{query}%",)
        if owner:
            args = (f"%{query}%", owner)
        cursor.execute(
            f"SELECT * FROM tasks WHERE content LIKE ? AND status != 'cancelled' {deleted_filter}{owner_filter} ORDER BY trigger_time ASC",
            args
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ── 提醒送达记录（App 轮询显示 AI 提醒原文）──────────────
    def add_reminder(self, task_id: int, content: str, message: str, owner: str = None):
        """记录一次已触发的提醒（AI 生成的原文）。owner：任务归属人。"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reminders (task_id, content, message, owner) VALUES (?, ?, ?, ?)",
            (task_id, content, message, owner),
        )
        conn.commit()
        conn.close()

    def list_reminders(self, after_id: int = 0, limit: int = 20, owner: str = None) -> List[dict]:
        """列出 reminder_id > after_id 的提醒记录（最新在前）。
        owner 指定时只返回 TA 的提醒（隐私：不混入别人的任务内容）。"""
        conn = get_db()
        cursor = conn.cursor()
        if owner:
            cursor.execute(
                """SELECT reminder_id, task_id, content, message, created_at
                   FROM reminders WHERE reminder_id > ? AND owner = ?
                   ORDER BY reminder_id DESC LIMIT ?""",
                (after_id, owner, limit),
            )
        else:
            cursor.execute(
                """SELECT reminder_id, task_id, content, message, created_at
                   FROM reminders WHERE reminder_id > ?
                   ORDER BY reminder_id DESC LIMIT ?""",
                (after_id, limit),
            )
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ── 看护告警（watchdog 发现的问题，App 轮询显示）──────────
    def add_alert(self, alert_type: str, message: str, owner: str = None):
        """记录一条看护告警。owner：涉及哪个说话人的会话（可空=全局诊断）。"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO alerts (type, message, owner) VALUES (?, ?, ?)",
                       (alert_type, message, owner))
        conn.commit()
        conn.close()

    def alert_exists(self, message: str, hours: int = 24) -> bool:
        """判断同一条告警 24h 内是否已记录过（避免每个 tick 重复报）。"""
        conn = get_db()
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT alert_id FROM alerts WHERE message=? AND created_at >= datetime('now','localtime', ?)",
            (message, f"-{hours} hours"),
        ).fetchone()
        conn.close()
        return row is not None

    def list_alerts(self, after_id: int = 0, limit: int = 20, owner: str = None) -> List[dict]:
        """列出 alert_id > after_id 的告警（最新在前）。
        owner 指定时只返回 TA 的告警（不泄露别人的会话内容）。"""
        conn = get_db()
        cursor = conn.cursor()
        if owner:
            cursor.execute(
                """SELECT alert_id, type, message, created_at
                   FROM alerts WHERE alert_id > ? AND owner = ?
                   ORDER BY alert_id DESC LIMIT ?""",
                (after_id, owner, limit),
            )
        else:
            cursor.execute(
                """SELECT alert_id, type, message, created_at
                   FROM alerts WHERE alert_id > ?
                   ORDER BY alert_id DESC LIMIT ?""",
                (after_id, limit),
            )
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]


class ConversationManager:
    """Manages conversation history and pending slot states"""
    
    def __init__(self):
        init_db()
    
    def add_message(self, session_id: str, role: str, content: str,
                    intent: str = None, slots: dict = None, speaker: str = None,
                    emotion: str = None, event: str = None):
        """Add a message to conversation history (speaker/emotion/event 存独立列；speaker 同时写 slots 兼容旧读取)"""
        if speaker:
            slots = dict(slots or {})
            slots["speaker"] = speaker
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO conversations (session_id, role, content, intent, slots, emotion, event, speaker)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (session_id, role, content, intent, json.dumps(slots) if slots else None,
              emotion, event, speaker))
        conn.commit()
        conn.close()
    
    def get_history(self, session_id: str, limit: int = 10) -> list:
        """Get recent conversation history for a session (NEWEST `limit` messages, chronological)."""
        conn = get_db()
        cursor = conn.cursor()
        # DESC + 外部反转：取最新的 limit 条，再按时间正序返回给调用方
        cursor.execute("""
            SELECT * FROM (
                SELECT * FROM conversations
                WHERE session_id = ?
                ORDER BY conv_id DESC LIMIT ?
            ) ORDER BY conv_id ASC
        """, (session_id, limit))
        rows = cursor.fetchall()
        conn.close()
        
        messages = []
        for row in rows:
            msg = {"role": row["role"], "content": row["content"]}
            if row["intent"]:
                msg["intent"] = row["intent"]
            if row["created_at"]:
                msg["created_at"] = row["created_at"]
            # 说话人：优先独立列，旧数据回退 slots JSON
            sp = row["speaker"] if "speaker" in row.keys() else None
            if not sp and row["slots"]:
                try:
                    sp = json.loads(row["slots"]).get("speaker") or None
                except Exception:
                    sp = None
            if sp:
                msg["speaker"] = sp
            # 情绪 / 声音事件（语音消息才带）
            for key in ("emotion", "event"):
                val = row[key] if key in row.keys() else None
                if val:
                    msg[key] = val
            messages.append(msg)
        return messages
    
    def get_context_for_ai(self, session_id: str, limit: int = 6) -> list:
        """Get conversation context formatted for AI API"""
        history = self.get_history(session_id, limit)
        return [{"role": m["role"], "content": m["content"]} for m in history]
    
    def save_pending_slots(self, session_id: str, partial_slots: dict, 
                           missing_slots: list, original_message: str = None,
                           intent: str = "ADD_TASK"):
        """Save partially filled slots awaiting user completion"""
        # Remove any existing pending for this session
        self.clear_pending_slots(session_id)
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO pending_slots (session_id, intent, partial_slots, missing_slots, original_message)
        VALUES (?, ?, ?, ?, ?)
        """, (session_id, intent, json.dumps(partial_slots), json.dumps(missing_slots), original_message))
        conn.commit()
        conn.close()
    
    def get_pending_slots(self, session_id: str) -> Optional[dict]:
        """Get pending slots for a session"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM pending_slots 
        WHERE session_id = ? 
        ORDER BY created_at DESC LIMIT 1
        """, (session_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "intent": row["intent"] if "intent" in row.keys() else "ADD_TASK",
                "partial_slots": json.loads(row["partial_slots"]),
                "missing_slots": json.loads(row["missing_slots"]),
                "original_message": row["original_message"]
            }
        return None
    
    def clear_pending_slots(self, session_id: str):
        """Clear pending slots for a session"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pending_slots WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()

    def list_sessions(self) -> list:
        """List all distinct sessions with title, last activity, and message count.
        Most recent first.
        """
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT session_id,
                   COUNT(*)                          AS msg_count,
                   MAX(created_at)                   AS last_at,
                   (SELECT content FROM conversations c2
                    WHERE c2.session_id = c.session_id AND c2.role = 'user'
                    ORDER BY c2.conv_id ASC LIMIT 1) AS first_user_msg
            FROM conversations c
            GROUP BY session_id
            ORDER BY last_at DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        sessions = []
        conn2 = get_db()
        cursor2 = conn2.cursor()
        for row in rows:
            sid = row["session_id"]
            if sid.startswith("speaker_"):
                # 隐私：说话人专属会话只显示「X 的聊天」，绝不显示消息内容/自定义标题
                name = sid[len("speaker_"):]
                title = f"{name} 的聊天"
            else:
                # 匿名会话：不显示消息内容；有自定义标题才显示，否则「匿名聊天」
                custom = cursor2.execute("SELECT title FROM session_titles WHERE session_id = ?",
                                         (sid,)).fetchone()
                title = (custom["title"] if custom else "匿名聊天")[:40]
            sessions.append({
                "session_id": sid,
                "title": title,
                "last_at": row["last_at"],
                "msg_count": row["msg_count"],
            })
        conn2.close()
        # 匿名会话只保留最新一个（侧边栏一个"匿名聊天"就够，避免一堆临时会话）
        speaker_sessions = [s for s in sessions if s["session_id"].startswith("speaker_")]
        anon_sessions = [s for s in sessions if not s["session_id"].startswith("speaker_")]
        if anon_sessions:
            latest_anon = max(anon_sessions, key=lambda s: s["last_at"] or "")
            sessions = speaker_sessions + [latest_anon]
        else:
            sessions = speaker_sessions
        sessions.sort(key=lambda s: s["last_at"] or "", reverse=True)
        return sessions

    def rename_session(self, session_id: str, title: str):
        """Set (or update) a custom title for a session."""
        title = (title or "").strip()[:40]
        if not title:
            return False
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO session_titles (session_id, title) VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET title = excluded.title,
                                                  updated_at = datetime('now', 'localtime')
        """, (session_id, title))
        conn.commit()
        conn.close()
        return True

    def delete_session(self, session_id: str):
        """Delete a session and all its conversations + pending slots."""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM pending_slots WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()
