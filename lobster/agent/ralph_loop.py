"""
🐦 灵雀 - Ralph Loop 持久化自主循环

升级特性：
- LLM 智能判断任务完成（不再依赖关键词）
- 任务优先级系统（high/medium/low）
- 智能进度摘要
- 完成度评估

Ralph Loop 是一种持续运行的自主 Agent 循环，适用于：
- 长期监控任务（价格变化、网站更新、仓库动态）
- 需要多次迭代的研究任务
- 定期汇报/检查类任务

与普通 ReAct 循环的区别：
- 普通循环：收到用户消息 → 执行 → 返回结果 → 结束
- Ralph Loop：启动任务 → 持续运行 → 定期检查 → 自主决策 → 直到目标完成或用户停止
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field, fields, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from ..llm import LLMRouter

logger = logging.getLogger("lingque.ralph")


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"      # 等待启动
    RUNNING = "running"      # 正在运行
    PAUSED = "paused"        # 已暂停
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 失败
    CANCELLED = "cancelled"  # 已取消


class TaskPriority(str, Enum):
    """任务优先级"""
    HIGH = "high"        # 高优先级：间隔更短，优先执行
    MEDIUM = "medium"    # 中优先级：正常执行
    LOW = "low"          # 低优先级：间隔更长，延后执行


# 优先级对应的间隔倍数
_PRIORITY_INTERVAL_MULTIPLIER = {
    TaskPriority.HIGH: 0.5,    # 高优先级：间隔减半
    TaskPriority.MEDIUM: 1.0,  # 中优先级：正常
    TaskPriority.LOW: 2.0,     # 低优先级：间隔翻倍
}


@dataclass
class RalphTask:
    """自主循环任务"""
    id: str
    name: str
    goal: str                          # 任务目标描述
    chat_id: str                       # 结果发送到哪个会话
    creator_id: str = ""               # 创建者 ID
    
    check_interval_minutes: int = 30   # 检查间隔（分钟）
    max_iterations: int = 100          # 最大迭代次数（防止无限运行）
    priority: TaskPriority = TaskPriority.MEDIUM  # 任务优先级
    
    status: TaskStatus = TaskStatus.PENDING
    current_iteration: int = 0
    completion_score: float = 0.0      # LLM 评估的完成度 (0-1)
    
    # 任务上下文（LLM 记住之前的进展）
    context_summary: str = ""
    last_result: str = ""
    
    # 成功指标（用于 LLM 判断完成）
    success_criteria: str = ""         # 用户定义的成功标准
    
    # 时间戳
    created_at: str = ""
    started_at: str = ""
    last_check_at: str = ""
    completed_at: str = ""
    
    # 历史日志
    history: list = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """转换为可序列化的字典"""
        d = asdict(self)
        d["status"] = self.status.value
        d["priority"] = self.priority.value
        return d
    
    @classmethod
    def from_dict(cls, d: dict) -> "RalphTask":
        """从字典恢复（容忍多余/缺失字段）"""
        d = d.copy()
        d["status"] = TaskStatus(d.get("status", "pending"))
        d["priority"] = TaskPriority(d.get("priority", "medium"))
        if "history" not in d:
            d["history"] = []
        if "completion_score" not in d:
            d["completion_score"] = 0.0
        if "success_criteria" not in d:
            d["success_criteria"] = ""
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)
    
    def add_history(self, action: str, result: str = ""):
        """添加历史记录"""
        self.history.append({
            "time": datetime.now().isoformat(),
            "iteration": self.current_iteration,
            "action": action,
            "result": result[:500] if result else "",
        })
        # 只保留最近 20 条
        if len(self.history) > 20:
            self.history = self.history[-20:]
    
    def get_effective_interval(self) -> float:
        """获取考虑优先级后的实际检查间隔"""
        multiplier = _PRIORITY_INTERVAL_MULTIPLIER.get(self.priority, 1.0)
        return self.check_interval_minutes * multiplier


class RalphLoop:
    """
    Ralph Loop 管理器 v2.0
    
    升级特性：
    - LLM 智能判断任务完成
    - 优先级调度
    - 智能进度评估
    
    负责管理所有自主循环任务的生命周期
    """
    
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._tasks_file = self.data_dir / "ralph_tasks.json"
        
        self.tasks: dict[str, RalphTask] = {}
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        
        # 回调函数（由 main.py 注入）
        self._agent = None
        self._llm: Optional["LLMRouter"] = None  # 用于智能判断
        self._send_callback: Optional[Callable] = None
        
    def set_agent(self, agent):
        """注入 Agent 实例"""
        self._agent = agent
        # 同时获取 LLM Router
        if hasattr(agent, 'llm'):
            self._llm = agent.llm
        
    def set_llm(self, llm: "LLMRouter"):
        """直接注入 LLM Router"""
        self._llm = llm
        
    def set_send_callback(self, callback: Callable):
        """注入消息发送回调"""
        self._send_callback = callback
    
    def _save_tasks(self):
        """持久化任务到磁盘"""
        data = {tid: t.to_dict() for tid, t in self.tasks.items()}
        self._tasks_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.debug(f"Ralph 任务已保存: {len(self.tasks)} 个")
    
    def _load_tasks(self):
        """从磁盘加载任务"""
        if not self._tasks_file.exists():
            return
        try:
            data = json.loads(self._tasks_file.read_text(encoding="utf-8"))
            for tid, tdata in data.items():
                self.tasks[tid] = RalphTask.from_dict(tdata)
            logger.info(f"Ralph 任务已恢复: {len(self.tasks)} 个")
        except Exception as e:
            logger.error(f"加载 Ralph 任务失败: {e}")
    
    async def start(self):
        """启动 Ralph Loop 后台循环"""
        self._load_tasks()
        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())
        logger.info("🔄 Ralph Loop 已启动")
    
    async def stop(self):
        """停止 Ralph Loop"""
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        self._save_tasks()
        logger.info("🔄 Ralph Loop 已停止")
    
    async def _run_loop(self):
        """主循环：定期检查并执行任务（按优先级排序）"""
        while self._running:
            try:
                now = datetime.now()
                
                # 获取所有运行中的任务，按优先级排序
                running_tasks = [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]
                running_tasks.sort(key=lambda t: (
                    {"high": 0, "medium": 1, "low": 2}.get(t.priority.value, 1),
                    t.last_check_at or ""
                ))
                
                for task in running_tasks:
                    # 检查是否到了执行时间（考虑优先级）
                    effective_interval = task.get_effective_interval()
                    if task.last_check_at:
                        last_check = datetime.fromisoformat(task.last_check_at)
                        minutes_since = (now - last_check).total_seconds() / 60
                        if minutes_since < effective_interval:
                            continue
                    
                    # 检查迭代次数
                    if task.current_iteration >= task.max_iterations:
                        task.status = TaskStatus.COMPLETED
                        task.completed_at = now.isoformat()
                        task.completion_score = 1.0
                        task.add_history("max_iterations", "达到最大迭代次数，任务自动完成")
                        await self._notify(task, "📊 任务已完成", f"任务 **{task.name}** 达到最大迭代次数 ({task.max_iterations})，已自动完成。")
                        self._save_tasks()
                        continue
                    
                    # 执行一次迭代
                    await self._execute_iteration(task)
                
            except Exception as e:
                logger.error(f"Ralph Loop 循环异常: {e}", exc_info=True)
            
            # 每分钟检查一次
            await asyncio.sleep(60)
    
    async def _execute_iteration(self, task: RalphTask):
        """执行一次任务迭代"""
        if not self._agent:
            logger.warning(f"Ralph 任务 {task.name} 跳过: Agent 未就绪")
            return
        
        task.current_iteration += 1
        task.last_check_at = datetime.now().isoformat()
        
        logger.info(f"Ralph 执行迭代: {task.name} (#{task.current_iteration}, 优先级: {task.priority.value})")
        task.add_history("iteration_start", f"开始第 {task.current_iteration} 次迭代")
        
        # 构建 prompt，包含任务上下文
        prompt = self._build_iteration_prompt(task)
        
        try:
            # 使用独立的 session_id
            session_id = f"ralph:{task.id}"
            result = await self._agent.process_message(
                prompt,
                session_id=session_id,
                user_id=task.creator_id or "ralph",
            )
            
            task.last_result = result
            task.add_history("iteration_complete", result[:300])
            
            # LLM 智能判断完成度和是否完成
            completion_result = await self._llm_check_completion(task, result)
            task.completion_score = completion_result["score"]
            should_complete = completion_result["completed"]
            
            if should_complete:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now().isoformat()
                task.add_history("task_completed", f"任务目标已达成 (完成度: {task.completion_score:.0%})")
                await self._notify(
                    task, 
                    "✅ 任务完成", 
                    f"任务 **{task.name}** 已完成！\n"
                    f"完成度评估: {task.completion_score:.0%}\n\n"
                    f"{result[:500]}"
                )
            else:
                # 更新上下文摘要（用于下次迭代）
                task.context_summary = await self._llm_summarize_progress(task)
                
                # 根据完成度决定汇报频率
                report_interval = 5 if task.completion_score < 0.5 else 3
                if task.current_iteration % report_interval == 0:
                    await self._notify(
                        task, 
                        "📊 任务进度", 
                        f"任务 **{task.name}** 进行中...\n"
                        f"已执行 {task.current_iteration} 次迭代\n"
                        f"完成度: {task.completion_score:.0%}\n\n"
                        f"最新结果:\n{result[:300]}..."
                    )
            
            self._save_tasks()
            
        except Exception as e:
            logger.error(f"Ralph 任务 {task.name} 执行失败: {e}")
            task.add_history("iteration_error", str(e))
            
            # 连续失败 3 次则暂停
            recent_errors = sum(1 for h in task.history[-5:] if h.get("action") == "iteration_error")
            if recent_errors >= 3:
                task.status = TaskStatus.PAUSED
                task.add_history("auto_paused", "连续失败 3 次，任务已自动暂停")
                await self._notify(task, "⚠️ 任务暂停", f"任务 **{task.name}** 连续失败 3 次，已自动暂停。\n错误: {e}")
            
            self._save_tasks()
    
    def _build_iteration_prompt(self, task: RalphTask) -> str:
        """构建迭代 prompt"""
        parts = [
            f"[Ralph Loop 自主任务 - 第 {task.current_iteration} 次迭代]",
            f"任务名称: {task.name}",
            f"任务目标: {task.goal}",
        ]
        
        if task.context_summary:
            parts.append(f"\n之前的进展摘要:\n{task.context_summary}")
        
        if task.last_result:
            parts.append(f"\n上次执行结果:\n{task.last_result[:500]}")
        
        parts.append(
            "\n请继续执行任务。"
            "如果任务目标已完成，请在回复中明确说明'任务已完成'或'目标已达成'。"
            "如果需要继续，请执行下一步操作并汇报进展。"
        )
        
        return "\n".join(parts)
    
    async def _llm_check_completion(self, task: RalphTask, result: str) -> dict:
        """
        LLM 智能判断任务完成度
        
        Returns:
            {"score": 0.0-1.0, "completed": bool, "reason": str}
        """
        # 如果没有 LLM，降级到关键词检测
        if not self._llm:
            completed = self._keyword_check_completion(result)
            return {"score": 1.0 if completed else 0.0, "completed": completed, "reason": "关键词匹配"}
        
        try:
            from ..llm.base import Message
            
            # 构建评估 prompt
            eval_prompt = f"""请评估以下自主任务的完成情况。

任务目标: {task.goal}
{f"成功标准: {task.success_criteria}" if task.success_criteria else ""}

当前执行结果:
{result[:1000]}

请以 JSON 格式回答（只输出 JSON，不要其他内容）:
{{
    "completion_score": 0.0到1.0之间的数字，表示任务完成度,
    "is_completed": true或false，任务目标是否已完全达成,
    "reason": "简短说明判断理由"
}}"""

            response = await self._llm.chat(
                messages=[Message(role="user", content=eval_prompt)],
                tools=None,
                system_prompt="你是一个任务评估助手，根据任务目标和执行结果判断完成情况。只输出 JSON。",
            )
            
            # 解析响应
            import re
            content = response.content or ""
            # 提取 JSON
            json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                score = float(data.get("completion_score", 0))
                completed = bool(data.get("is_completed", False))
                reason = data.get("reason", "")
                
                # 完成度超过 0.9 且标记完成
                if score >= 0.9 and completed:
                    return {"score": score, "completed": True, "reason": reason}
                
                return {"score": score, "completed": False, "reason": reason}
            
        except Exception as e:
            logger.warning(f"LLM 完成度评估失败，降级到关键词: {e}")
        
        # 降级到关键词检测
        completed = self._keyword_check_completion(result)
        return {"score": 1.0 if completed else task.completion_score, "completed": completed, "reason": "关键词匹配(降级)"}
    
    def _keyword_check_completion(self, result: str) -> bool:
        """关键词检测完成（降级方案）"""
        completion_indicators = [
            "任务已完成", "目标已达成", "任务完成", "已完成目标",
            "task completed", "goal achieved", "mission accomplished",
        ]
        result_lower = result.lower()
        return any(ind.lower() in result_lower for ind in completion_indicators)
    
    async def _llm_summarize_progress(self, task: RalphTask) -> str:
        """LLM 智能生成进度摘要"""
        # 如果没有 LLM，使用简单摘要
        if not self._llm:
            return self._simple_summarize_progress(task)
        
        try:
            from ..llm.base import Message
            
            recent_history = task.history[-5:]
            history_text = "\n".join([
                f"- [{h.get('action', '?')}] {h.get('result', '')[:150]}"
                for h in recent_history
            ])
            
            summary_prompt = f"""请为以下自主任务生成简洁的进度摘要（用于下次迭代时提供上下文）。

任务目标: {task.goal}
已执行迭代: {task.current_iteration} 次
当前完成度: {task.completion_score:.0%}

最近执行历史:
{history_text}

最新结果:
{task.last_result[:500]}

请用 2-3 句话总结：已完成什么、当前状态、下一步应该做什么。"""

            response = await self._llm.chat(
                messages=[Message(role="user", content=summary_prompt)],
                tools=None,
                system_prompt="你是一个任务进度总结助手，请简洁准确地总结任务进展。",
            )
            
            if response.content:
                return response.content[:500]
            
        except Exception as e:
            logger.warning(f"LLM 进度摘要失败: {e}")
        
        return self._simple_summarize_progress(task)
    
    def _simple_summarize_progress(self, task: RalphTask) -> str:
        """简单进度摘要（降级方案）"""
        recent = task.history[-5:]
        if not recent:
            return ""
        
        lines = [f"已执行 {task.current_iteration} 次迭代，完成度 {task.completion_score:.0%}。最近动作:"]
        for h in recent:
            lines.append(f"- [{h.get('action', '?')}] {h.get('result', '')[:100]}")
        return "\n".join(lines)
    
    async def _notify(self, task: RalphTask, title: str, content: str):
        """发送通知给用户"""
        if self._send_callback and task.chat_id:
            try:
                await self._send_callback(task.chat_id, title, content)
            except Exception as e:
                logger.error(f"发送 Ralph 通知失败: {e}")
    
    # ==================== 任务管理 API ====================
    
    def create_task(
        self,
        name: str,
        goal: str,
        chat_id: str,
        creator_id: str = "",
        check_interval_minutes: int = 30,
        max_iterations: int = 100,
        priority: str = "medium",
        success_criteria: str = "",
    ) -> RalphTask:
        """创建新任务"""
        task_id = str(uuid.uuid4())[:8]
        
        # 解析优先级
        try:
            task_priority = TaskPriority(priority)
        except ValueError:
            task_priority = TaskPriority.MEDIUM
        
        task = RalphTask(
            id=task_id,
            name=name,
            goal=goal,
            chat_id=chat_id,
            creator_id=creator_id,
            check_interval_minutes=check_interval_minutes,
            max_iterations=max_iterations,
            priority=task_priority,
            success_criteria=success_criteria,
            created_at=datetime.now().isoformat(),
        )
        self.tasks[task_id] = task
        self._save_tasks()
        logger.info(f"创建 Ralph 任务: {name} (ID: {task_id}, 优先级: {priority})")
        return task
    
    def update_priority(self, task_id: str, priority: str) -> bool:
        """更新任务优先级"""
        task = self.tasks.get(task_id)
        if not task:
            return False
        
        try:
            task.priority = TaskPriority(priority)
            task.add_history("priority_changed", f"优先级更新为 {priority}")
            self._save_tasks()
            return True
        except ValueError:
            return False
    
    def start_task(self, task_id: str) -> bool:
        """启动任务"""
        task = self.tasks.get(task_id)
        if not task:
            return False
        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return False
        
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now().isoformat()
        task.add_history("started", "任务已启动")
        self._save_tasks()
        logger.info(f"启动 Ralph 任务: {task.name}")
        return True
    
    def pause_task(self, task_id: str) -> bool:
        """暂停任务"""
        task = self.tasks.get(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return False
        
        task.status = TaskStatus.PAUSED
        task.add_history("paused", "任务已暂停")
        self._save_tasks()
        logger.info(f"暂停 Ralph 任务: {task.name}")
        return True
    
    def resume_task(self, task_id: str) -> bool:
        """恢复任务"""
        task = self.tasks.get(task_id)
        if not task or task.status != TaskStatus.PAUSED:
            return False
        
        task.status = TaskStatus.RUNNING
        task.add_history("resumed", "任务已恢复")
        self._save_tasks()
        logger.info(f"恢复 Ralph 任务: {task.name}")
        return True
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        task = self.tasks.get(task_id)
        if not task:
            return False
        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return False
        
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now().isoformat()
        task.add_history("cancelled", "任务已取消")
        self._save_tasks()
        logger.info(f"取消 Ralph 任务: {task.name}")
        return True
    
    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        if task_id not in self.tasks:
            return False
        
        task = self.tasks.pop(task_id)
        self._save_tasks()
        logger.info(f"删除 Ralph 任务: {task.name}")
        return True
    
    def get_task(self, task_id: str) -> Optional[RalphTask]:
        """获取任务"""
        return self.tasks.get(task_id)
    
    def list_tasks(self, status: Optional[TaskStatus] = None) -> list[RalphTask]:
        """列出任务"""
        tasks = list(self.tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)
    
    def get_status_summary(self) -> str:
        """获取状态摘要"""
        if not self.tasks:
            return "🔄 没有自主循环任务"
        
        by_status = {}
        for task in self.tasks.values():
            s = task.status.value
            by_status[s] = by_status.get(s, 0) + 1
        
        lines = [f"🔄 Ralph Loop 任务 ({len(self.tasks)} 个):\n"]
        
        status_emoji = {
            "running": "🟢", "paused": "⏸️", "pending": "⏳",
            "completed": "✅", "failed": "❌", "cancelled": "🚫",
        }
        
        for task in self.list_tasks()[:10]:  # 只显示最近 10 个
            emoji = status_emoji.get(task.status.value, "❓")
            iter_info = f"#{task.current_iteration}" if task.current_iteration > 0 else ""
            lines.append(f"{emoji} **{task.name}** {iter_info}")
            lines.append(f"   ID: {task.id} | 间隔: {task.check_interval_minutes}分钟")
        
        return "\n".join(lines)


# 全局实例
_ralph_loop: Optional[RalphLoop] = None


def get_ralph_loop() -> Optional[RalphLoop]:
    """获取 Ralph Loop 实例"""
    return _ralph_loop


def init_ralph_loop(data_dir: Path) -> RalphLoop:
    """初始化 Ralph Loop"""
    global _ralph_loop
    _ralph_loop = RalphLoop(data_dir)
    return _ralph_loop
