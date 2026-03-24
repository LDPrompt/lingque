"""
灵雀 - 多 Agent 自动组队系统

灵雀作为"老板"，自动检测可并行拆分的复杂任务，
通过 LLM 生成角色分工，创建轻量子 Agent 并行执行，汇总结果。

子 Agent 共享主 Agent 的 llm_router，拥有独立的角色 prompt 和消息历史，
不是完整的 Agent 实例，而是轻量的 ReAct 循环。
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable

logger = logging.getLogger("lingque.agent.multi")


class SubTaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentRole:
    """子 Agent 角色定义"""
    name: str
    persona: str
    task: str
    skills: list[str] = field(default_factory=list)
    max_loops: int = 8


@dataclass
class SubTask:
    """子任务（绑定角色）"""
    id: str
    role: AgentRole
    status: SubTaskStatus = SubTaskStatus.PENDING
    result: str = ""
    error: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


@dataclass
class TeamPlan:
    """团队执行计划"""
    original_request: str
    subtasks: list[SubTask]
    parallel: bool = True
    merge_strategy: str = "summarize"


_TEAM_KEYWORDS = re.compile(
    r"组.{0,2}团队|组.{0,2}队|找.{0,3}助手|多.{0,2}agent|multi.?agent"
    r"|招募|分工|协作|团队模式",
    re.IGNORECASE,
)

_PARALLEL_PATTERNS = [
    r"分别|各自|同时|并行|一起",
    r"(?:A|B|C|甲|乙|丙).{0,8}(?:和|与|、|跟).{0,8}(?:B|C|丙)",
    r"(\d+)\s*(?:个|家|款|种).{0,6}(?:竞品|产品|品牌|公司|网站|平台|渠道|方案)",
    r"(?:对比|比较|调研|分析|监控|采集|抓取).{0,6}(?:多个|几个|\d+个)",
]

TEAM_PLANNING_PROMPT = """你是灵雀的任务规划引擎。分析用户请求，判断是否适合拆分为多个独立子任务并行执行。

用户请求:
{request}

## 判断规则
1. 只有子任务之间**相互独立**（不需要等其他子任务结果）才拆分
2. 单一目标的任务（如"写一篇文章"）不要拆分
3. 每个子任务需要指定角色名、角色人格、具体任务、所需技能类别

## 可用技能类别
browser（浏览器/网页操作）, web（网络搜索）, search（搜索）, file（文件操作）,
code（代码执行）, memory（记忆）, feishu（飞书）, email（邮件）,
scheduler（定时任务）, general（通用）

请以 JSON 格式返回:
{{
  "should_split": true/false,
  "reason": "拆分/不拆分的原因",
  "parallel": true/false,
  "merge_strategy": "summarize",
  "roles": [
    {{
      "name": "角色名称（如：数据采集员）",
      "persona": "角色人格描述（1-2句话，定义工作风格）",
      "task": "该角色的具体任务指令（清晰、可独立执行）",
      "skills": ["browser", "web"]
    }}
  ]
}}

注意:
- roles 数组最多 4 个角色
- 如果不适合拆分，should_split 设为 false，roles 留空数组
- 每个角色的 task 必须是完整、独立可执行的指令

只返回 JSON，不要其他内容。"""


MERGE_PROMPT = """你是灵雀的结果汇总专家。多个子 Agent 已经并行完成了各自的任务，请将结果整合为一个清晰、连贯的最终回答。

## 原始用户请求
{request}

## 各角色的执行结果
{results}

## 要求
1. 整合所有结果为一个统一的回复，不要简单拼接
2. 如果某个角色失败了，说明该部分未完成
3. 保持信息完整，语言简洁
4. 不要提及"子Agent"、"角色"等内部概念，像一个人完成的一样自然"""


class MultiAgentController:
    """
    多 Agent 控制器

    负责:
    - 判断任务是否适合多 Agent 处理
    - 通过 LLM 规划角色和分工
    - 创建轻量子 Agent 并行执行
    - 收集和汇总结果
    """

    def __init__(
        self,
        llm_router,
        context_builder,
        max_parallel: int = 3,
        sub_agent_timeout: int = 180,
    ):
        self.llm_router = llm_router
        self.context_builder = context_builder
        self.max_parallel = max_parallel
        self.sub_agent_timeout = sub_agent_timeout

        self._current_team: Optional[TeamPlan] = None
        self._progress_callback: Optional[Callable] = None

    def set_progress_callback(self, callback):
        self._progress_callback = callback

    @staticmethod
    def should_use_team(user_input: str) -> bool:
        """判断用户输入是否适合使用多 Agent 团队模式"""
        if len(user_input) < 15:
            return False

        if _TEAM_KEYWORDS.search(user_input):
            return True

        score = 0
        for pat in _PARALLEL_PATTERNS:
            if re.search(pat, user_input):
                score += 1
        if score >= 1:
            action_kw = ["对比", "比较", "调研", "分析", "监控", "采集",
                         "抓取", "搜索", "查找", "整理"]
            if any(k in user_input for k in action_kw):
                return True

        items = re.findall(r"[、，,]\s*", user_input)
        if len(items) >= 2:
            multi_target_kw = ["竞品", "产品", "平台", "网站", "品牌", "渠道",
                               "公司", "方案", "价格", "行情"]
            if any(k in user_input for k in multi_target_kw):
                return True

        return False

    async def process(self, request: str) -> Optional[str]:
        """
        处理用户请求：规划 → 组队 → 并行执行 → 汇总

        返回汇总结果字符串；如果不适合团队模式则返回 None（由主 Agent 降级处理）
        """
        plan = await self._plan_team(request)
        if plan is None:
            return None

        self._current_team = plan
        n = len(plan.subtasks)
        role_names = [t.role.name for t in plan.subtasks]
        logger.info(f"团队组建完成: {n} 个角色 = {role_names}")

        if self._progress_callback:
            try:
                team_msg = "、".join(role_names)
                await self._progress_callback(
                    1, n + 1, f"组建团队: {team_msg}", "running"
                )
            except Exception:
                pass

        if plan.parallel:
            results = await self._execute_parallel(plan.subtasks)
        else:
            results = await self._execute_sequential(plan.subtasks)

        merged = await self._merge_results(plan, results)
        self._current_team = None
        return merged

    async def _plan_team(self, request: str) -> Optional[TeamPlan]:
        """用 LLM 规划团队角色和分工"""
        from ..llm.base import Message

        prompt = TEAM_PLANNING_PROMPT.format(request=request)
        try:
            resp = await asyncio.wait_for(
                self.llm_router.chat(
                    messages=[Message(role="user", content=prompt)],
                    temperature=0.3,
                ),
                timeout=20,
            )

            text = resp.content.strip()
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                logger.warning("团队规划: LLM 未返回有效 JSON")
                return None

            data = json.loads(match.group())

            if not data.get("should_split", False):
                logger.info(f"团队规划: LLM 判断不需要拆分 - {data.get('reason', '')}")
                return None

            roles_data = data.get("roles", [])
            if len(roles_data) < 2:
                logger.info("团队规划: 角色少于 2 个，不使用团队模式")
                return None

            subtasks = []
            for rd in roles_data[:4]:
                role = AgentRole(
                    name=rd.get("name", "助手"),
                    persona=rd.get("persona", ""),
                    task=rd.get("task", ""),
                    skills=rd.get("skills", ["general"]),
                )
                subtasks.append(SubTask(
                    id=f"sub_{uuid.uuid4().hex[:8]}",
                    role=role,
                ))

            return TeamPlan(
                original_request=request,
                subtasks=subtasks,
                parallel=data.get("parallel", True),
                merge_strategy=data.get("merge_strategy", "summarize"),
            )

        except asyncio.TimeoutError:
            logger.warning("团队规划超时(20s)")
            return None
        except Exception as e:
            logger.warning(f"团队规划失败: {e}")
            return None

    async def _run_sub_agent(self, task: SubTask) -> str:
        """
        运行一个轻量子 Agent：独立 ReAct 循环

        子 Agent 共享 llm_router，拥有独立消息历史和角色 system prompt，
        可以调用分配给它的技能子集。
        """
        from ..llm.base import Message
        from ..skills.registry import registry as skill_registry

        role = task.role
        system_prompt = self.context_builder.build_sub_agent_prompt(role)
        tools = skill_registry.get_tools_by_categories(role.skills)

        messages: list[Message] = [
            Message(role="user", content=role.task),
        ]

        final_result = ""
        for step in range(role.max_loops):
            try:
                resp = await asyncio.wait_for(
                    self.llm_router.chat(
                        messages=messages,
                        tools=tools if tools else None,
                        system_prompt=system_prompt,
                    ),
                    timeout=60,
                )
            except asyncio.TimeoutError:
                logger.warning(f"子Agent [{role.name}] 第{step+1}步 LLM 超时")
                break
            except Exception as e:
                logger.warning(f"子Agent [{role.name}] 第{step+1}步 LLM 异常: {e}")
                break

            if resp.content and not resp.tool_calls:
                final_result = resp.content
                break

            if resp.tool_calls:
                messages.append(Message(
                    role="assistant",
                    content=resp.content or "",
                    tool_calls=resp.tool_calls,
                ))

                for tc in resp.tool_calls:
                    logger.info(f"子Agent [{role.name}] 调用工具: {tc.name}")
                    try:
                        result_str = await asyncio.wait_for(
                            skill_registry.execute(tc.name, tc.arguments),
                            timeout=60,
                        )
                    except asyncio.TimeoutError:
                        result_str = f"工具 {tc.name} 执行超时"
                    except Exception as e:
                        result_str = f"工具 {tc.name} 执行失败: {e}"

                    if len(result_str) > 8000:
                        result_str = result_str[:8000] + "\n... [结果已截断]"

                    messages.append(Message(
                        role="tool",
                        content=result_str,
                        name=tc.name,
                        tool_call_id=tc.id,
                    ))
            elif resp.content:
                final_result = resp.content
                break
            else:
                break

        if not final_result:
            for msg in reversed(messages):
                if msg.role == "assistant" and msg.content:
                    final_result = msg.content
                    break
            if not final_result:
                final_result = "(该角色未产生有效输出)"

        return final_result

    async def _execute_parallel(self, tasks: list[SubTask]) -> list[str]:
        """并行执行多个子 Agent"""
        semaphore = asyncio.Semaphore(self.max_parallel)

        async def run_one(task: SubTask) -> str:
            async with semaphore:
                task.status = SubTaskStatus.RUNNING
                logger.info(f"子Agent [{task.role.name}] 开始执行")
                start = time.monotonic()
                try:
                    result = await asyncio.wait_for(
                        self._run_sub_agent(task),
                        timeout=self.sub_agent_timeout,
                    )
                    task.status = SubTaskStatus.COMPLETED
                    task.result = result
                    task.completed_at = datetime.now()
                    elapsed = time.monotonic() - start
                    logger.info(f"子Agent [{task.role.name}] 完成 ({elapsed:.1f}s)")

                    if self._progress_callback:
                        done_count = sum(
                            1 for t in self._current_team.subtasks
                            if t.status in (SubTaskStatus.COMPLETED, SubTaskStatus.FAILED)
                        ) if self._current_team else 0
                        total = len(self._current_team.subtasks) if self._current_team else 1
                        try:
                            await self._progress_callback(
                                done_count + 1, total + 1,
                                f"{task.role.name} 已完成", "running",
                            )
                        except Exception:
                            pass

                    return result
                except asyncio.TimeoutError:
                    task.status = SubTaskStatus.FAILED
                    task.error = "执行超时"
                    task.completed_at = datetime.now()
                    logger.warning(f"子Agent [{task.role.name}] 超时 ({self.sub_agent_timeout}s)")
                    return f"({task.role.name} 执行超时)"
                except Exception as e:
                    task.status = SubTaskStatus.FAILED
                    task.error = str(e)
                    task.completed_at = datetime.now()
                    logger.warning(f"子Agent [{task.role.name}] 异常: {e}")
                    return f"({task.role.name} 执行失败: {e})"

        results = await asyncio.gather(*[run_one(t) for t in tasks])
        return list(results)

    async def _execute_sequential(self, tasks: list[SubTask]) -> list[str]:
        """串行执行多个子 Agent"""
        results = []
        for i, task in enumerate(tasks):
            task.status = SubTaskStatus.RUNNING
            logger.info(f"子Agent [{task.role.name}] 开始执行 ({i+1}/{len(tasks)})")
            try:
                result = await asyncio.wait_for(
                    self._run_sub_agent(task),
                    timeout=self.sub_agent_timeout,
                )
                task.status = SubTaskStatus.COMPLETED
                task.result = result
            except asyncio.TimeoutError:
                task.status = SubTaskStatus.FAILED
                task.error = "执行超时"
                result = f"({task.role.name} 执行超时)"
            except Exception as e:
                task.status = SubTaskStatus.FAILED
                task.error = str(e)
                result = f"({task.role.name} 执行失败: {e})"
            task.completed_at = datetime.now()
            results.append(result)
        return results

    async def _merge_results(self, plan: TeamPlan, results: list[str]) -> str:
        """用 LLM 汇总多个子 Agent 的结果"""
        from ..llm.base import Message

        results_text = ""
        for task, result in zip(plan.subtasks, results):
            status_label = "完成" if task.status == SubTaskStatus.COMPLETED else "失败"
            results_text += f"\n### {task.role.name}（{status_label}）\n{result}\n"

        prompt = MERGE_PROMPT.format(
            request=plan.original_request,
            results=results_text,
        )

        try:
            resp = await asyncio.wait_for(
                self.llm_router.chat(
                    messages=[Message(role="user", content=prompt)],
                ),
                timeout=30,
            )
            return resp.content or results_text
        except Exception as e:
            logger.warning(f"结果汇总失败，使用拼接: {e}")
            output = f"## 任务完成\n\n原始请求: {plan.original_request}\n"
            for task, result in zip(plan.subtasks, results):
                output += f"\n### {task.role.name}\n{result}\n"
            return output

    def get_status(self) -> str:
        """获取当前团队状态"""
        if not self._current_team:
            return "当前没有团队在执行任务。"

        plan = self._current_team
        lines = [f"**当前团队** ({len(plan.subtasks)} 个角色)\n"]
        for task in plan.subtasks:
            icon = {
                SubTaskStatus.PENDING: "⏳",
                SubTaskStatus.RUNNING: "🔄",
                SubTaskStatus.COMPLETED: "✅",
                SubTaskStatus.FAILED: "❌",
            }.get(task.status, "❓")
            elapsed = ""
            if task.completed_at and task.created_at:
                secs = (task.completed_at - task.created_at).total_seconds()
                elapsed = f" ({secs:.0f}s)"
            lines.append(f"{icon} **{task.role.name}** - {task.role.task[:60]}{elapsed}")

        return "\n".join(lines)
