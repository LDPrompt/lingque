"""
心跳系统 - 让 Agent 主动醒来检查待办事项

灵雀的核心差异化功能：Agent 不只是被动应答，
每隔 N 分钟自动醒来，检查是否有需要处理的事情：
  - 待执行的工作流（暂停中等待超时的）
  - 定时任务到期
  - 邮件监控
  - 自定义检查项

如果没有需要处理的事，安静退出，不打扰用户。
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable, Awaitable, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("lobster.heartbeat")


@dataclass
class HeartbeatCheck:
    """心跳检查项"""
    name: str
    callback: Callable[[], Awaitable[Optional[str]]]
    enabled: bool = True
    last_triggered: Optional[datetime] = None
    trigger_count: int = 0


class HeartbeatEngine:
    """
    心跳引擎 - 定时唤醒 Agent 主动工作

    与 CronScheduler 的区别：
    - Cron 是固定时间点执行固定任务
    - 心跳是周期性"检查是否有事做"，有事才行动，没事安静

    用法：
        heartbeat = HeartbeatEngine(interval_minutes=30)
        heartbeat.add_check("待办检查", check_pending_tasks)
        heartbeat.set_action_callback(agent_process_message)
        await heartbeat.start()
    """

    def __init__(self, interval_minutes: int = 30):
        self.interval_minutes = interval_minutes
        self._checks: dict[str, HeartbeatCheck] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._action_callback: Optional[Callable] = None
        self._notify_callback: Optional[Callable] = None
        self._chat_id_getter: Optional[Callable] = None
        self._beat_count = 0
        self._last_beat: Optional[datetime] = None

    def set_action_callback(self, callback: Callable):
        """设置 Agent 行动回调（有事做时调用）"""
        self._action_callback = callback

    def set_notify_callback(self, callback: Callable):
        """设置通知回调（直接发消息，不经过 Agent）"""
        self._notify_callback = callback

    def set_chat_id_getter(self, getter: Callable):
        """设置获取当前 chat_id 的回调"""
        self._chat_id_getter = getter

    def add_check(self, name: str, callback: Callable[[], Awaitable[Optional[str]]]):
        """
        注册心跳检查项

        callback 应返回:
          - None 或 "": 没有需要处理的事
          - 非空字符串: 需要 Agent 处理的任务描述
        """
        self._checks[name] = HeartbeatCheck(name=name, callback=callback)
        logger.info(f"注册心跳检查: {name}")

    def remove_check(self, name: str):
        self._checks.pop(name, None)

    async def start(self):
        """启动心跳引擎"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"💓 心跳引擎已启动 (间隔: {self.interval_minutes} 分钟, "
                     f"检查项: {len(self._checks)})")

    async def stop(self):
        """停止心跳引擎"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("💓 心跳引擎已停止")

    async def _heartbeat_loop(self):
        """心跳主循环"""
        await asyncio.sleep(60)

        while self._running:
            try:
                await self._beat()
            except Exception as e:
                logger.error(f"心跳异常: {e}", exc_info=True)

            await asyncio.sleep(self.interval_minutes * 60)

    async def _beat(self):
        """单次心跳"""
        self._beat_count += 1
        self._last_beat = datetime.now()
        logger.debug(f"💓 心跳 #{self._beat_count}")

        action_items = []

        for check in self._checks.values():
            if not check.enabled:
                continue
            try:
                result = await check.callback()
                if result and result.strip():
                    action_items.append((check.name, result))
                    check.last_triggered = datetime.now()
                    check.trigger_count += 1
                    logger.info(f"心跳检查 [{check.name}] 触发: {result[:100]}")
            except Exception as e:
                logger.error(f"心跳检查 [{check.name}] 失败: {e}")

        if not action_items:
            logger.debug("💓 心跳完成，无待处理事项")
            return

        prompt = self._build_prompt(action_items)
        logger.info(f"💓 心跳发现 {len(action_items)} 项待处理")

        if self._action_callback:
            try:
                response = await self._action_callback(prompt)
                if response and self._notify_callback:
                    chat_id = ""
                    if self._chat_id_getter:
                        chat_id = self._chat_id_getter()
                    if chat_id:
                        await self._notify_callback(
                            chat_id,
                            f"💓 **主动提醒**\n\n{response}"
                        )
            except Exception as e:
                logger.error(f"心跳行动失败: {e}")

    def _build_prompt(self, items: list[tuple[str, str]]) -> str:
        """构建发送给 Agent 的提示"""
        parts = [
            "[系统心跳] 以下事项需要你关注和处理：\n"
        ]
        for i, (name, desc) in enumerate(items, 1):
            parts.append(f"{i}. 【{name}】{desc}")

        parts.append(
            "\n请逐一处理上述事项。如果某项不需要立即行动，"
            "简要说明原因。处理完后给出简洁的汇总。"
        )
        return "\n".join(parts)

    def get_status(self) -> str:
        """获取心跳状态"""
        lines = [
            f"💓 **心跳引擎状态**",
            f"运行中: {'是' if self._running else '否'}",
            f"间隔: {self.interval_minutes} 分钟",
            f"心跳次数: {self._beat_count}",
        ]
        if self._last_beat:
            lines.append(f"上次心跳: {self._last_beat.strftime('%H:%M:%S')}")

        if self._checks:
            lines.append(f"\n**检查项 ({len(self._checks)}):**")
            for check in self._checks.values():
                status = "✅" if check.enabled else "⏸️"
                triggered = (f"上次触发: {check.last_triggered.strftime('%H:%M')}"
                             if check.last_triggered else "未触发")
                lines.append(f"  {status} {check.name} ({triggered}, 共 {check.trigger_count} 次)")

        return "\n".join(lines)


# ==================== 内置检查项 ====================

async def check_paused_workflows() -> Optional[str]:
    """检查是否有暂停超时的工作流"""
    try:
        from ..skills.workflow_skills import get_workflow_engine
        engine = get_workflow_engine()
        if not engine:
            return None

        paused = engine.list_runs(status="paused")
        if not paused:
            return None

        overdue = []
        now = datetime.now()
        for run in paused:
            created = datetime.fromisoformat(run.get("created_at", ""))
            if (now - created) > timedelta(hours=2):
                overdue.append(f"{run['workflow_name']} (ID: {run['id']})")

        if overdue:
            return f"有 {len(overdue)} 个工作流暂停超过 2 小时待审批: " + ", ".join(overdue)
        return None
    except Exception:
        return None


async def check_pending_cron_tasks() -> Optional[str]:
    """检查是否有即将执行的定时任务"""
    try:
        from ..main import get_scheduler
        scheduler = get_scheduler()
        if not scheduler:
            return None

        upcoming = []
        now = datetime.now()
        for task in scheduler.tasks.values():
            if task.enabled and task.next_run and task.next_run <= now + timedelta(minutes=5):
                upcoming.append(task.name)

        if upcoming:
            return f"有 {len(upcoming)} 个定时任务即将执行: " + ", ".join(upcoming)
        return None
    except Exception:
        return None


async def check_daily_notes() -> Optional[str]:
    """检查今天是否有未处理的每日笔记提醒"""
    return None
