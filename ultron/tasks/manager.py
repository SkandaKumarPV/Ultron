"""ULTRON Task and Reminder Manager"""
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json

from ultron.config import config
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    OVERDUE = "overdue"


class TaskPriority(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    URGENT = 4


@dataclass
class Task:
    task_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    due_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    workspace_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Reminder:
    reminder_id: str
    task_id: str
    message: str
    trigger_at: datetime
    created_at: datetime = field(default_factory=datetime.now)
    fired: bool = False
    fired_at: Optional[datetime] = None
    recurrence: Optional[str] = None  # "daily", "weekly", "monthly", None
    windows_task_name: Optional[str] = None  # For Windows Task Scheduler


class TaskManager:
    """Manages tasks and reminders with local SQLite storage"""

    def __init__(self):
        self._db_path = config.resolve_path("tasks.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self):
        """Initialize database tables"""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    due_date TEXT,
                    completed_at TEXT,
                    workspace_id TEXT,
                    tags TEXT,
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    reminder_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    trigger_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    fired INTEGER DEFAULT 0,
                    fired_at TEXT,
                    recurrence TEXT,
                    windows_task_name TEXT,
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_trigger_at ON reminders (trigger_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks (workspace_id)
            """)

    def _row_to_task(self, row) -> Task:
        return Task(
            task_id=row[0],
            title=row[1],
            description=row[2] or "",
            status=TaskStatus(row[3]),
            priority=TaskPriority(row[4]),
            created_at=datetime.fromisoformat(row[5]),
            updated_at=datetime.fromisoformat(row[6]),
            due_date=datetime.fromisoformat(row[7]) if row[7] else None,
            completed_at=datetime.fromisoformat(row[8]) if row[8] else None,
            workspace_id=row[9],
            tags=json.loads(row[10]) if row[10] else [],
            metadata=json.loads(row[11]) if row[11] else {},
        )

    def _row_to_reminder(self, row) -> Reminder:
        return Reminder(
            reminder_id=row[0],
            task_id=row[1],
            message=row[2],
            trigger_at=datetime.fromisoformat(row[3]),
            created_at=datetime.fromisoformat(row[4]),
            fired=bool(row[5]),
            fired_at=datetime.fromisoformat(row[6]) if row[6] else None,
            recurrence=row[7],
            windows_task_name=row[8],
        )

    # ========== Task Operations ==========

    def create_task(self,
                    title: str,
                    description: str = "",
                    priority: TaskPriority = TaskPriority.MEDIUM,
                    due_date: Optional[datetime] = None,
                    workspace_id: Optional[str] = None,
                    tags: List[str] = None) -> Task:
        """Create a new task"""
        task_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        task = Task(
            task_id=task_id,
            title=title,
            description=description,
            priority=priority,
            due_date=due_date,
            workspace_id=workspace_id,
            tags=tags or [],
        )

        # Convert priority to int if it's an enum
        priority_val = task.priority.value if hasattr(task.priority, 'value') else task.priority
        status_val = task.status.value if hasattr(task.status, 'value') else task.status
        
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT INTO tasks (task_id, title, description, status, priority, created_at, updated_at, due_date, completed_at, workspace_id, tags, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.task_id, task.title, task.description, status_val,
                priority_val, task.created_at.isoformat(), task.updated_at.isoformat(),
                task.due_date.isoformat() if task.due_date else None,
                task.completed_at.isoformat() if task.completed_at else None,
                task.workspace_id, json.dumps(task.tags), json.dumps(task.metadata)
            ))

        audit_log(AuditEventType.TOOL_INVOKED, severity=AuditSeverity.INFO,
                  workspace_id=workspace_id,
                  tool_name="task_manager.create_task", action="create",
                  result="success", details={"task_id": task_id, "title": title})

        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID"""
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row:
                return self._row_to_task(row)
        return None

    def list_tasks(self,
                   workspace_id: Optional[str] = None,
                   status: Optional[TaskStatus] = None,
                   limit: int = 50) -> List[Task]:
        """List tasks with optional filters"""
        query = "SELECT * FROM tasks WHERE 1=1"
        params = []

        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY priority DESC, due_date ASC, created_at DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_task(row) for row in rows]

    def update_task(self, task_id: str, **kwargs) -> Optional[Task]:
        """Update task fields"""
        allowed_fields = {"title", "description", "status", "priority", "due_date", "tags", "metadata"}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return self.get_task(task_id)

        updates["updated_at"] = datetime.now().isoformat()

        # Handle enum conversions
        if "status" in updates and isinstance(updates["status"], TaskStatus):
            updates["status"] = updates["status"].value
        if "priority" in updates and isinstance(updates["priority"], TaskPriority):
            updates["priority"] = updates["priority"].value
        if "due_date" in updates and updates["due_date"]:
            updates["due_date"] = updates["due_date"].isoformat()
        if "tags" in updates:
            updates["tags"] = json.dumps(updates["tags"])
        if "metadata" in updates:
            updates["metadata"] = json.dumps(updates["metadata"])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(f"UPDATE tasks SET {set_clause} WHERE task_id = ?", values)

        audit_log(AuditEventType.TOOL_INVOKED, severity=AuditSeverity.INFO,
                  workspace_id=None,
                  tool_name="task_manager.update_task", action="update",
                  result="success", details={"task_id": task_id, "fields": list(kwargs.keys())})

        return self.get_task(task_id)

    def delete_task(self, task_id: str) -> bool:
        """Delete a task and its reminders"""
        with sqlite3.connect(self._db_path) as conn:
            # Delete reminders first
            conn.execute("DELETE FROM reminders WHERE task_id = ?", (task_id,))
            cursor = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            deleted = cursor.rowcount > 0

        if deleted:
            audit_log(AuditEventType.TOOL_INVOKED, severity=AuditSeverity.INFO,
                      tool_name="task_manager.delete_task", action="delete",
                      result="success", details={"task_id": task_id})

        return deleted

    def complete_task(self, task_id: str) -> Optional[Task]:
        """Mark task as completed"""
        return self.update_task(task_id, status=TaskStatus.COMPLETED, completed_at=datetime.now())

    # ========== Reminder Operations ==========

    def create_reminder(self,
                        task_id: str,
                        message: str,
                        trigger_at: datetime,
                        recurrence: Optional[str] = None) -> Reminder:
        """Create a reminder for a task"""
        # Verify task exists
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        reminder_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        reminder = Reminder(
            reminder_id=reminder_id,
            task_id=task_id,
            message=message,
            trigger_at=trigger_at,
            created_at=now,
            recurrence=recurrence,
        )

        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT INTO reminders (reminder_id, task_id, message, trigger_at, created_at, fired, recurrence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                reminder.reminder_id, reminder.task_id, reminder.message,
                reminder.trigger_at.isoformat(), reminder.created_at.isoformat(),
                int(reminder.fired), reminder.recurrence
            ))

        # Schedule in Windows Task Scheduler
        self._schedule_windows_reminder(reminder)

        audit_log(AuditEventType.TOOL_INVOKED, severity=AuditSeverity.INFO,
                  tool_name="task_manager.create_reminder", action="create",
                  result="success", details={"reminder_id": reminder_id, "task_id": task_id})

        return reminder

    def _schedule_windows_reminder(self, reminder: Reminder) -> bool:
        """Schedule reminder in Windows Task Scheduler"""
        try:
            import subprocess
            task_name = f"ULTRON_Reminder_{reminder.reminder_id}"

            # Convert trigger time to Windows format
            trigger_time = reminder.trigger_at.strftime("%H:%M")
            trigger_date = reminder.trigger_at.strftime("%Y-%m-%d")

            # Create the task
            cmd = [
                "schtasks", "/create", "/tn", task_name,
                "/tr", f'powershell -Command "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak(\'{reminder.message}\'); Write-Host \'{reminder.message}\'"',
                "/sc", "once",
                "/st", trigger_time,
                "/sd", trigger_date,
                "/f"  # force overwrite
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                # Update reminder with Windows task name
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "UPDATE reminders SET windows_task_name = ? WHERE reminder_id = ?",
                        (task_name, reminder.reminder_id)
                    )
                return True
            else:
                audit_log(AuditEventType.ERROR, severity=AuditSeverity.WARNING,
                          tool_name="task_manager._schedule_windows_reminder",
                          result="failed", details={"error": result.stderr, "task_name": task_name})
                return False

        except Exception as e:
            audit_log(AuditEventType.ERROR, severity=AuditSeverity.ERROR,
                      tool_name="task_manager._schedule_windows_reminder",
                      result="failed", details={"error": str(e)})
            return False

    def get_reminder(self, reminder_id: str) -> Optional[Reminder]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute("SELECT * FROM reminders WHERE reminder_id = ?", (reminder_id,)).fetchone()
            if row:
                return self._row_to_reminder(row)
        return None

    def list_reminders(self,
                       task_id: Optional[str] = None,
                       fired: Optional[bool] = None,
                       upcoming_only: bool = False) -> List[Reminder]:
        query = "SELECT * FROM reminders WHERE 1=1"
        params = []

        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        if fired is not None:
            query += " AND fired = ?"
            params.append(int(fired))
        if upcoming_only:
            query += " AND trigger_at >= ?"
            params.append(datetime.now().isoformat())

        query += " ORDER BY trigger_at ASC"

        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_reminder(row) for row in rows]

    def cancel_reminder(self, reminder_id: str) -> bool:
        """Cancel a reminder (remove from Windows Task Scheduler too)"""
        reminder = self.get_reminder(reminder_id)
        if not reminder:
            return False

        # Remove from Windows Task Scheduler
        if reminder.windows_task_name:
            try:
                import subprocess
                subprocess.run(["schtasks", "/delete", "/tn", reminder.windows_task_name, "/f"],
                               capture_output=True, timeout=5)
            except Exception:
                pass

        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM reminders WHERE reminder_id = ?", (reminder_id,))

        audit_log(AuditEventType.TOOL_INVOKED, severity=AuditSeverity.INFO,
                  tool_name="task_manager.cancel_reminder", action="cancel",
                  result="success", details={"reminder_id": reminder_id})
        return True

    def get_due_reminders(self) -> List[Reminder]:
        """Get reminders that are due to fire"""
        now = datetime.now().isoformat()
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE fired = 0 AND trigger_at <= ?", (now,)
            ).fetchall()
            return [self._row_to_reminder(row) for row in rows]

    def fire_reminder(self, reminder_id: str) -> bool:
        """Mark reminder as fired"""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                UPDATE reminders SET fired = 1, fired_at = ? WHERE reminder_id = ?
            """, (datetime.now().isoformat(), reminder_id))
            return conn.rowcount > 0


# Global instance
task_manager = TaskManager()