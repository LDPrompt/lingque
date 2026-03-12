"""
🐦 灵雀 - Cron 定时任务调度器 (P2)

支持:
- Cron 表达式定时触发
- 每日摘要推送
- 自定义定时任务
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable, Any
from dataclasses import dataclass, field

try:
    from croniter import croniter
except ImportError:
    croniter = None

logger = logging.getLogger("lingque.scheduler")


@dataclass
class ScheduledTask:
    """定时任务"""
    name: str
    cron_expr: str  # Cron 表达式，如 "0 8 * * *" 每天8点
    callback: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    enabled: bool = True
    last_run: datetime = None
    next_run: datetime = None


class CronScheduler:
    """
    Cron 定时任务调度器

    用法:
        scheduler = CronScheduler()
        scheduler.add_task("每日摘要", "0 8 * * *", daily_summary_callback)
        await scheduler.start()
    """

    def __init__(self):
        self.tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._task: asyncio.Task = None

    def add_task(
        self,
        name: str,
        cron_expr: str,
        callback: Callable,
        *args,
        **kwargs
    ):
        """添加定时任务"""
        if croniter is None:
            logger.error("croniter 未安装，无法使用 Cron 调度器。请运行: pip install croniter")
            return

        task = ScheduledTask(
            name=name,
            cron_expr=cron_expr,
            callback=callback,
            args=args,
            kwargs=kwargs,
        )
        task.next_run = self._get_next_run(cron_expr)
        self.tasks[name] = task
        logger.info(f"已添加定时任务: {name} ({cron_expr}), 下次执行: {task.next_run}")

    def remove_task(self, name: str):
        """移除定时任务"""
        if name in self.tasks:
            del self.tasks[name]
            logger.info(f"已移除定时任务: {name}")

    def _get_next_run(self, cron_expr: str) -> datetime:
        """计算下次执行时间"""
        if croniter is None:
            return None
        cron = croniter(cron_expr, datetime.now())
        return cron.get_next(datetime)

    async def start(self):
        """启动调度器"""
        if croniter is None:
            logger.error("croniter 未安装，调度器无法启动")
            return

        self._running = True
        logger.info(f"Cron 调度器已启动，共 {len(self.tasks)} 个任务")
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """停止调度器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Cron 调度器已停止")

    async def _run_loop(self):
        """调度循环"""
        while self._running:
            now = datetime.now()

            for task in list(self.tasks.values()):
                if not task.enabled or task.next_run is None:
                    continue

                if now >= task.next_run:
                    logger.info(f"执行定时任务: {task.name}")
                    try:
                        if asyncio.iscoroutinefunction(task.callback):
                            await task.callback(*task.args, **task.kwargs)
                        else:
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(
                                None, lambda: task.callback(*task.args, **task.kwargs)
                            )
                        task.last_run = now
                    except Exception as e:
                        logger.error(f"定时任务 {task.name} 执行失败: {e}")

                    task.next_run = self._get_next_run(task.cron_expr)
                    logger.info(f"任务 {task.name} 下次执行: {task.next_run}")

            await asyncio.sleep(30)  # 每 30 秒检查一次

    def get_status(self) -> str:
        """获取调度器状态"""
        if not self.tasks:
            return "📅 没有定时任务"

        lines = [f"📅 定时任务 ({len(self.tasks)} 个):\n"]
        for task in self.tasks.values():
            status = "✅" if task.enabled else "⏸️"
            next_run = task.next_run.strftime("%m-%d %H:%M") if task.next_run else "未计算"
            lines.append(f"{status} {task.name}\n   Cron: {task.cron_expr}\n   下次: {next_run}")
        return "\n".join(lines)


# ==================== 预置任务: 每日摘要 ====================

class DailySummary:
    """每日摘要生成器"""

    def __init__(self, agent, send_callback: Callable, get_chat_id: Callable = None):
        self.agent = agent
        self.send_callback = send_callback
        self._get_chat_id = get_chat_id

    async def generate_and_send(self, chat_id: str = ""):
        """生成并发送每日摘要"""
        target = chat_id
        if not target and self._get_chat_id:
            target = self._get_chat_id()
        if not target:
            logger.warning("每日摘要跳过: 尚无活跃的会话 (还没人跟灵雀说过话)")
            return

        logger.info(f"开始生成每日摘要，目标会话: {target}")

        prompt = """请帮我生成今日摘要，包含以下内容:
1. 查看今天的日历日程
2. 查看未读邮件摘要（最多5封）
3. 查看待发送的提醒

请用简洁的格式汇总这些信息。"""

        try:
            response = await self.agent.process_message(prompt)
            await self.send_callback(target, "🌅 早安，今日摘要", response)
            logger.info("每日摘要已发送")
        except Exception as e:
            logger.error(f"生成每日摘要失败: {e}")
