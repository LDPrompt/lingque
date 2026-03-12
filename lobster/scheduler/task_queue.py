"""
🐦 灵雀 - 任务队列 (P2)

功能:
- 消息排队处理，避免并发冲突
- 长任务异步化，后台执行
- 任务状态追踪
"""

import asyncio
import logging
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable, Any
from enum import Enum

logger = logging.getLogger("lingque.task_queue")


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """任务实体"""
    id: str
    name: str
    callback: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime = None
    completed_at: datetime = None
    result: Any = None
    error: str = None
    chat_id: str = ""  # 完成后通知的会话 ID
    notify_on_complete: bool = False


class TaskQueue:
    """
    异步任务队列

    功能:
    - 串行处理消息，避免并发冲突
    - 支持长任务后台执行
    - 任务完成后主动通知

    用法:
        queue = TaskQueue(notify_callback)
        await queue.start()
        task_id = await queue.submit("处理数据", process_data, arg1, arg2, notify=True)
    """

    def __init__(self, notify_callback: Callable = None, max_workers: int = 1):
        self.notify_callback = notify_callback
        self.max_workers = max_workers
        self._queue: asyncio.Queue = asyncio.Queue()
        self._tasks: dict[str, Task] = {}
        self._running = False
        self._workers: list[asyncio.Task] = []

    async def start(self):
        """启动任务队列"""
        self._running = True
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        logger.info(f"任务队列已启动，{self.max_workers} 个 worker")

    async def stop(self):
        """停止任务队列"""
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        logger.info("任务队列已停止")

    async def submit(
        self,
        name: str,
        callback: Callable,
        *args,
        chat_id: str = "",
        notify: bool = False,
        **kwargs
    ) -> str:
        """
        提交任务到队列

        Args:
            name: 任务名称
            callback: 要执行的函数
            args: 位置参数
            chat_id: 完成后通知的会话 ID
            notify: 是否在完成后通知
            kwargs: 关键字参数

        Returns:
            task_id: 任务 ID
        """
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task = Task(
            id=task_id,
            name=name,
            callback=callback,
            args=args,
            kwargs=kwargs,
            chat_id=chat_id,
            notify_on_complete=notify,
        )
        self._tasks[task_id] = task
        await self._queue.put(task)
        logger.info(f"任务已提交: {name} (ID: {task_id})")
        return task_id

    async def _worker(self, worker_id: int):
        """工作协程"""
        while self._running:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now()
            logger.info(f"Worker-{worker_id} 开始执行: {task.name}")

            try:
                if asyncio.iscoroutinefunction(task.callback):
                    task.result = await task.callback(*task.args, **task.kwargs)
                else:
                    loop = asyncio.get_running_loop()
                    task.result = await loop.run_in_executor(
                        None, lambda: task.callback(*task.args, **task.kwargs)
                    )
                task.status = TaskStatus.COMPLETED
                logger.info(f"任务完成: {task.name}")
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                logger.error(f"任务失败: {task.name} - {e}")

            task.completed_at = datetime.now()

            # 完成后通知
            if task.notify_on_complete and self.notify_callback and task.chat_id:
                await self._send_notification(task)

            self._queue.task_done()

    async def _send_notification(self, task: Task):
        """发送任务完成通知"""
        if task.status == TaskStatus.COMPLETED:
            msg = f"✅ 任务完成: {task.name}\n\n"
            if task.result:
                result_str = str(task.result)[:500]
                msg += f"结果:\n{result_str}"
        else:
            msg = f"❌ 任务失败: {task.name}\n\n错误: {task.error}"

        try:
            await self.notify_callback(task.chat_id, msg)
        except Exception as e:
            logger.error(f"发送任务通知失败: {e}")

    def get_task(self, task_id: str) -> Task:
        """获取任务状态"""
        return self._tasks.get(task_id)

    def get_status(self) -> str:
        """获取队列状态"""
        pending = sum(1 for t in self._tasks.values() if t.status == TaskStatus.PENDING)
        running = sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)
        completed = sum(1 for t in self._tasks.values() if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in self._tasks.values() if t.status == TaskStatus.FAILED)

        return (
            f"📋 任务队列状态\n"
            f"  待处理: {pending}\n"
            f"  执行中: {running}\n"
            f"  已完成: {completed}\n"
            f"  失败: {failed}"
        )

    def clear_completed(self):
        """清理已完成的任务记录"""
        to_remove = [
            tid for tid, task in self._tasks.items()
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
        ]
        for tid in to_remove:
            del self._tasks[tid]
        logger.info(f"已清理 {len(to_remove)} 个已完成任务")
