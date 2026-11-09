"""ULTRON Task Tools - Task and Reminder management via tool registry"""
from typing import List, Optional
from datetime import datetime

from ultron.tools.registry import BaseTool, ToolCategory, ToolParameter, ToolResult, tool_registry
from ultron.security.permissions import PermissionLevel
from ultron.tasks.manager import task_manager, Task, TaskStatus, TaskPriority, Reminder
from ultron.security.audit import audit_log, AuditEventType, AuditSeverity


class CreateTaskTool(BaseTool):
    """Create a new task"""
    name = "create_task"
    description = "Create a new task with optional due date and priority"
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.CREATE
    parameters = [
        ToolParameter(name="title", type="string", description="Task title"),
        ToolParameter(name="description", type="string", description="Task description", required=False, default=""),
        ToolParameter(name="priority", type="string", description="Priority level", required=False, default="medium",
                      enum=["low", "medium", "high", "urgent"]),
        ToolParameter(name="due_date", type="string", description="Due date in ISO format (YYYY-MM-DDTHH:MM:SS)", required=False),
        ToolParameter(name="tags", type="array", description="Tags for the task", required=False, default=[]),
    ]

    def execute(self, title: str, description: str = "", priority: str = "medium",
                due_date: str = None, tags: List[str] = None) -> ToolResult:
        try:
            prio = TaskPriority[priority.upper()]
        except KeyError:
            return ToolResult(success=False, error=f"Invalid priority: {priority}")

        due_dt = None
        if due_date:
            try:
                due_dt = datetime.fromisoformat(due_date)
            except ValueError:
                return ToolResult(success=False, error=f"Invalid due_date format: {due_date}")

        task = task_manager.create_task(
            title=title,
            description=description,
            priority=prio,
            due_date=due_dt,
            tags=tags or [],
        )

        return ToolResult(
            success=True,
            data={
                "task_id": task.task_id,
                "title": task.title,
                "status": task.status.value,
                "priority": task.priority.name,
                "due_date": task.due_date.isoformat() if task.due_date else None,
            },
            verification={"task_id": task.task_id, "action": "created"}
        )


class ListTasksTool(BaseTool):
    """List tasks with optional filters"""
    name = "list_tasks"
    description = "List tasks with optional filters"
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="status", type="string", description="Filter by status", required=False,
                      enum=["pending", "in_progress", "completed", "cancelled", "overdue"]),
        ToolParameter(name="limit", type="integer", description="Maximum tasks to return", required=False, default=20),
    ]

    def execute(self, status: str = None, limit: int = 20) -> ToolResult:
        status_enum = None
        if status:
            try:
                status_enum = TaskStatus[status.upper()]
            except KeyError:
                return ToolResult(success=False, error=f"Invalid status: {status}")

        tasks = task_manager.list_tasks(status=status_enum, limit=limit)

        return ToolResult(
            success=True,
            data={
                "tasks": [
                    {
                        "task_id": t.task_id,
                        "title": t.title,
                        "description": t.description,
                        "status": t.status.value,
                        "priority": t.priority.name,
                        "due_date": t.due_date.isoformat() if t.due_date else None,
                        "tags": t.tags,
                    }
                    for t in tasks
                ],
                "count": len(tasks)
            },
            verification={"action": "list_tasks", "count": len(tasks)}
        )


class GetTaskTool(BaseTool):
    """Get a specific task by ID"""
    name = "get_task"
    description = "Get task details by ID"
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="task_id", type="string", description="Task ID"),
    ]

    def execute(self, task_id: str) -> ToolResult:
        task = task_manager.get_task(task_id)
        if not task:
            return ToolResult(success=False, error=f"Task not found: {task_id}")

        return ToolResult(
            success=True,
            data={
                "task_id": task.task_id,
                "title": task.title,
                "description": task.description,
                "status": task.status.value,
                "priority": task.priority.name,
                "created_at": task.created_at.isoformat(),
                "updated_at": task.updated_at.isoformat(),
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "tags": task.tags,
                "metadata": task.metadata,
            },
            verification={"task_id": task_id, "action": "retrieved"}
        )


class UpdateTaskTool(BaseTool):
    """Update a task"""
    name = "update_task"
    description = "Update an existing task"
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.MODIFY
    parameters = [
        ToolParameter(name="task_id", type="string", description="Task ID"),
        ToolParameter(name="title", type="string", description="New title", required=False),
        ToolParameter(name="description", type="string", description="New description", required=False),
        ToolParameter(name="status", type="string", description="New status", required=False,
                      enum=["pending", "in_progress", "completed", "cancelled", "overdue"]),
        ToolParameter(name="priority", type="string", description="New priority", required=False,
                      enum=["low", "medium", "high", "urgent"]),
        ToolParameter(name="due_date", type="string", description="New due date (ISO format)", required=False),
        ToolParameter(name="tags", type="array", description="New tags", required=False),
    ]

    def execute(self, task_id: str, **kwargs) -> ToolResult:
        updates = {k: v for k, v in kwargs.items() if v is not None}

        if "status" in updates:
            try:
                updates["status"] = TaskStatus[updates["status"].upper()]
            except KeyError:
                return ToolResult(success=False, error=f"Invalid status: {updates['status']}")

        if "priority" in updates:
            try:
                updates["priority"] = TaskPriority[updates["priority"].upper()]
            except KeyError:
                return ToolResult(success=False, error=f"Invalid priority: {updates['priority']}")

        if "due_date" in updates and updates["due_date"]:
            try:
                updates["due_date"] = datetime.fromisoformat(updates["due_date"])
            except ValueError:
                return ToolResult(success=False, error=f"Invalid due_date format")

        task = task_manager.update_task(task_id, **updates)
        if not task:
            return ToolResult(success=False, error=f"Task not found: {task_id}")

        return ToolResult(
            success=True,
            data={
                "task_id": task.task_id,
                "title": task.title,
                "status": task.status.value,
                "priority": task.priority.name,
            },
            verification={"task_id": task_id, "action": "updated", "fields": list(kwargs.keys())}
        )


class CompleteTaskTool(BaseTool):
    """Mark a task as completed"""
    name = "complete_task"
    description = "Mark a task as completed"
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.MODIFY
    parameters = [
        ToolParameter(name="task_id", type="string", description="Task ID to complete"),
    ]

    def execute(self, task_id: str) -> ToolResult:
        task = task_manager.complete_task(task_id)
        if not task:
            return ToolResult(success=False, error=f"Task not found: {task_id}")

        return ToolResult(
            success=True,
            data={
                "task_id": task.task_id,
                "title": task.title,
                "status": task.status.value,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            },
            verification={"task_id": task_id, "action": "completed"}
        )


class DeleteTaskTool(BaseTool):
    """Delete a task"""
    name = "delete_task"
    description = "Delete a task and its reminders"
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.DELETE
    parameters = [
        ToolParameter(name="task_id", type="string", description="Task ID to delete"),
    ]

    def execute(self, task_id: str) -> ToolResult:
        success = task_manager.delete_task(task_id)
        if not success:
            return ToolResult(success=False, error=f"Task not found: {task_id}")

        return ToolResult(
            success=True,
            data={"task_id": task_id, "action": "deleted"},
            verification={"task_id": task_id, "action": "deleted"}
        )


class CreateReminderTool(BaseTool):
    """Create a reminder for a task"""
    name = "create_reminder"
    description = "Create a reminder for a task at a specific time"
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.CREATE
    parameters = [
        ToolParameter(name="task_id", type="string", description="Task ID to attach reminder to"),
        ToolParameter(name="message", type="string", description="Reminder message"),
        ToolParameter(name="trigger_at", type="string", description="When to trigger (ISO format: YYYY-MM-DDTHH:MM:SS)"),
        ToolParameter(name="recurrence", type="string", description="Recurrence pattern", required=False,
                      enum=["daily", "weekly", "monthly"]),
    ]

    def execute(self, task_id: str, message: str, trigger_at: str, recurrence: str = None) -> ToolResult:
        try:
            trigger_dt = datetime.fromisoformat(trigger_at)
        except ValueError:
            return ToolResult(success=False, error=f"Invalid trigger_at format: {trigger_at}")

        try:
            reminder = task_manager.create_reminder(
                task_id=task_id,
                message=message,
                trigger_at=trigger_dt,
                recurrence=recurrence,
            )
        except ValueError as e:
            return ToolResult(success=False, error=str(e))

        return ToolResult(
            success=True,
            data={
                "reminder_id": reminder.reminder_id,
                "task_id": reminder.task_id,
                "message": reminder.message,
                "trigger_at": reminder.trigger_at.isoformat(),
                "recurrence": reminder.recurrence,
            },
            verification={"reminder_id": reminder.reminder_id, "action": "created"}
        )


class ListRemindersTool(BaseTool):
    """List reminders"""
    name = "list_reminders"
    description = "List reminders with optional filters"
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.READ
    parameters = [
        ToolParameter(name="task_id", type="string", description="Filter by task ID", required=False),
        ToolParameter(name="upcoming_only", type="boolean", description="Only show upcoming reminders", required=False, default=True),
    ]

    def execute(self, task_id: str = None, upcoming_only: bool = True) -> ToolResult:
        reminders = task_manager.list_reminders(
            task_id=task_id,
            fired=False if upcoming_only else None,
            upcoming_only=upcoming_only
        )

        return ToolResult(
            success=True,
            data={
                "reminders": [
                    {
                        "reminder_id": r.reminder_id,
                        "task_id": r.task_id,
                        "message": r.message,
                        "trigger_at": r.trigger_at.isoformat(),
                        "recurrence": r.recurrence,
                        "fired": r.fired,
                    }
                    for r in reminders
                ],
                "count": len(reminders)
            },
            verification={"action": "list_reminders", "count": len(reminders)}
        )


class CancelReminderTool(BaseTool):
    """Cancel a reminder"""
    name = "cancel_reminder"
    description = "Cancel a reminder and remove from Windows Task Scheduler"
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.DELETE
    parameters = [
        ToolParameter(name="reminder_id", type="string", description="Reminder ID to cancel"),
    ]

    def execute(self, reminder_id: str) -> ToolResult:
        success = task_manager.cancel_reminder(reminder_id)
        if not success:
            return ToolResult(success=False, error=f"Reminder not found: {reminder_id}")

        return ToolResult(
            success=True,
            data={"reminder_id": reminder_id, "action": "cancelled"},
            verification={"reminder_id": reminder_id, "action": "cancelled"}
        )


# Register task tools
tool_registry.register(CreateTaskTool())
tool_registry.register(ListTasksTool())
tool_registry.register(GetTaskTool())
tool_registry.register(UpdateTaskTool())
tool_registry.register(CompleteTaskTool())
tool_registry.register(DeleteTaskTool())
tool_registry.register(CreateReminderTool())
tool_registry.register(ListRemindersTool())
tool_registry.register(CancelReminderTool())