"""
🐦 灵雀 - P3 主 Agent 系统

功能:
- 主 Agent 拆分子任务
- 子 Agent 并行处理
- 父子会话管理
- 结果汇总
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("lingque.agent.multi")


class SubTaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SubTask:
    """子任务"""
    id: str
    description: str
    prompt: str
    status: SubTaskStatus = SubTaskStatus.PENDING
    result: str = ""
    error: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


@dataclass
class TaskPlan:
    """任务计划"""
    original_request: str
    subtasks: list[SubTask]
    parallel: bool = True  # 是否并行执行
    merge_strategy: str = "combine"  # combine, select_best, summarize


PLANNING_PROMPT = """你是一个任务规划专家。分析用户的请求，将其拆分为可以独立执行的子任务。

用户请求:
{request}

请以 JSON 格式返回任务计划:
{{
  "parallel": true/false,  // 子任务是否可以并行执行
  "merge_strategy": "combine/select_best/summarize",  // 结果合并策略
  "subtasks": [
    {{
      "description": "子任务简述",
      "prompt": "给子 Agent 的具体指令"
    }}
  ]
}}

注意:
1. 只有真正需要拆分的复杂任务才拆分，简单任务直接返回单个子任务
2. 子任务应该独立可执行
3. 每个子任务的 prompt 要清晰具体

只返回 JSON，不要其他内容。"""


class MultiAgentController:
    """
    多 Agent 控制器

    负责:
    - 分析任务是否需要拆分
    - 创建子 Agent 并行执行
    - 收集和合并结果

    用法:
        controller = MultiAgentController(llm_router, agent_factory)
        result = await controller.process("帮我调研竞品A、B、C的功能")
    """

    def __init__(
        self,
        llm_router,
        agent_factory: Callable,  # 创建子 Agent 的工厂函数
        max_parallel: int = 3,
        timeout: int = 300,  # 子任务超时（秒）
    ):
        self.llm_router = llm_router
        self.agent_factory = agent_factory
        self.max_parallel = max_parallel
        self.timeout = timeout

        self._active_tasks: dict[str, SubTask] = {}

    async def process(self, request: str) -> str:
        """
        处理用户请求

        Args:
            request: 用户请求

        Returns:
            处理结果
        """
        # 1. 规划任务
        plan = await self._plan_tasks(request)

        if not plan.subtasks:
            return "无法理解任务，请换一种方式描述。"

        # 2. 如果只有一个子任务，直接执行
        if len(plan.subtasks) == 1:
            return await self._execute_single(plan.subtasks[0])

        # 3. 多个子任务，并行或串行执行
        logger.info(f"任务拆分为 {len(plan.subtasks)} 个子任务 (并行={plan.parallel})")

        if plan.parallel:
            results = await self._execute_parallel(plan.subtasks)
        else:
            results = await self._execute_sequential(plan.subtasks)

        # 4. 合并结果
        return await self._merge_results(plan, results)

    async def _plan_tasks(self, request: str) -> TaskPlan:
        """分析并规划任务"""
        import json
        import re

        prompt = PLANNING_PROMPT.format(request=request)

        try:
            from ..llm.base import Message
            response = await self.llm_router.chat(
                messages=[Message(role="user", content=prompt)],
                temperature=0.3,
            )

            # 解析 JSON
            text = response.content.strip()
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if not match:
                raise ValueError("未找到有效 JSON")

            data = json.loads(match.group())

            subtasks = []
            for i, st in enumerate(data.get("subtasks", [])):
                subtasks.append(SubTask(
                    id=f"subtask_{uuid.uuid4().hex[:8]}",
                    description=st.get("description", f"子任务 {i+1}"),
                    prompt=st.get("prompt", request),
                ))

            return TaskPlan(
                original_request=request,
                subtasks=subtasks,
                parallel=data.get("parallel", True),
                merge_strategy=data.get("merge_strategy", "combine"),
            )

        except Exception as e:
            logger.error(f"任务规划失败: {e}")
            # 降级：不拆分，作为单一任务
            return TaskPlan(
                original_request=request,
                subtasks=[SubTask(
                    id="single_task",
                    description="直接执行",
                    prompt=request,
                )],
            )

    async def _execute_single(self, task: SubTask) -> str:
        """执行单个任务"""
        try:
            agent = self.agent_factory()
            result = await asyncio.wait_for(
                agent.process_message(task.prompt),
                timeout=self.timeout,
            )
            task.status = SubTaskStatus.COMPLETED
            task.result = result
            return result
        except asyncio.TimeoutError:
            task.status = SubTaskStatus.FAILED
            task.error = "执行超时"
            return f"任务执行超时 ({self.timeout}s)"
        except Exception as e:
            task.status = SubTaskStatus.FAILED
            task.error = str(e)
            return f"任务执行失败: {e}"

    async def _execute_parallel(self, tasks: list[SubTask]) -> list[str]:
        """并行执行多个任务"""
        # 限制并发数
        semaphore = asyncio.Semaphore(self.max_parallel)

        async def run_with_semaphore(task: SubTask) -> str:
            async with semaphore:
                task.status = SubTaskStatus.RUNNING
                logger.info(f"开始子任务: {task.description}")
                result = await self._execute_single(task)
                task.completed_at = datetime.now()
                logger.info(f"完成子任务: {task.description}")
                return result

        results = await asyncio.gather(
            *[run_with_semaphore(t) for t in tasks],
            return_exceptions=True,
        )

        # 处理异常
        processed = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                tasks[i].status = SubTaskStatus.FAILED
                tasks[i].error = str(r)
                processed.append(f"子任务 {i+1} 失败: {r}")
            else:
                processed.append(r)

        return processed

    async def _execute_sequential(self, tasks: list[SubTask]) -> list[str]:
        """串行执行多个任务"""
        results = []
        for task in tasks:
            task.status = SubTaskStatus.RUNNING
            logger.info(f"开始子任务: {task.description}")
            result = await self._execute_single(task)
            task.completed_at = datetime.now()
            results.append(result)
            logger.info(f"完成子任务: {task.description}")
        return results

    async def _merge_results(self, plan: TaskPlan, results: list[str]) -> str:
        """合并子任务结果"""
        strategy = plan.merge_strategy

        if strategy == "combine":
            # 简单组合
            output = f"## 任务完成\n\n原始请求: {plan.original_request}\n\n"
            for i, (task, result) in enumerate(zip(plan.subtasks, results)):
                output += f"### {i+1}. {task.description}\n\n{result}\n\n"
            return output

        elif strategy == "select_best":
            # 选择最好的结果（这里简单选最长的）
            best = max(results, key=len)
            return best

        elif strategy == "summarize":
            # 用 LLM 总结
            combined = "\n\n---\n\n".join(results)
            prompt = f"""以下是多个子任务的执行结果，请汇总为一个统一的回答:

原始请求: {plan.original_request}

各子任务结果:
{combined}

请给出一个完整、连贯的最终回答:"""

            from ..llm.base import Message
            response = await self.llm_router.chat(
                messages=[Message(role="user", content=prompt)],
            )
            return response.content

        else:
            return "\n\n".join(results)

    def get_status(self) -> str:
        """获取当前状态"""
        if not self._active_tasks:
            return "🤖 多 Agent 系统空闲"

        lines = ["🤖 多 Agent 系统状态:\n"]
        for task in self._active_tasks.values():
            status_icon = {
                SubTaskStatus.PENDING: "⏳",
                SubTaskStatus.RUNNING: "🔄",
                SubTaskStatus.COMPLETED: "✅",
                SubTaskStatus.FAILED: "❌",
            }.get(task.status, "❓")
            lines.append(f"{status_icon} {task.description}")

        return "\n".join(lines)
