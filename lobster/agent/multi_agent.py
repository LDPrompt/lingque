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
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable

logger = logging.getLogger("lingque.agent.multi")


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

class SubTaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentRole:
    """子 Agent 角色定义"""
    name: str
    persona: str
    task: str
    skills: list[str] = field(default_factory=list)
    max_loops: int = 6


@dataclass
class SubTask:
    """子任务（绑定角色）"""
    id: str
    role: AgentRole
    status: SubTaskStatus = SubTaskStatus.PENDING
    result: str = ""
    error: str = ""
    side_effects: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


@dataclass
class TeamPlan:
    """团队执行计划"""
    original_request: str
    subtasks: list[SubTask]
    parallel: bool = True
    merge_strategy: str = "summarize"


# ---------------------------------------------------------------------------
# 共享黑板 —— 子 Agent 间轻量信息共享
# ---------------------------------------------------------------------------

class SharedBlackboard:
    """线程安全的共享黑板，子 Agent 可以发布/读取关键发现"""

    def __init__(self):
        self._entries: list[dict] = []
        self._lock = asyncio.Lock()

    async def post(self, author: str, key: str, value: str):
        async with self._lock:
            self._entries.append({
                "author": author,
                "key": key,
                "value": value[:500],
                "time": datetime.now().strftime("%H:%M:%S"),
            })

    async def read_all(self) -> str:
        async with self._lock:
            if not self._entries:
                return ""
            lines = []
            for e in self._entries:
                lines.append(f"- [{e['author']}] {e['key']}: {e['value']}")
            return "\n".join(lines)

    def clear(self):
        self._entries.clear()


# ---------------------------------------------------------------------------
# 触发检测
# ---------------------------------------------------------------------------

_TEAM_KEYWORDS = re.compile(
    r"组.{0,2}团队|组.{0,2}队|找.{0,3}助手|多.{0,2}agent|multi.?agent"
    r"|招募|分工|协作|团队模式",
    re.IGNORECASE,
)

_PARALLEL_PATTERNS = [
    r"(?:分别|各自|同时|并行).{0,8}(?:搜索|查找|调研|分析|采集|抓取|对比|比较|整理|监控)",
    r"(\d+)\s*(?:个|家|款|种).{0,6}(?:竞品|产品|品牌|公司|网站|平台|渠道|方案)",
    r"(?:对比|比较|调研|分析|监控|采集|抓取).{0,6}(?:多个|几个|\d+个)",
]

_SIMPLE_TASK_PATTERNS = re.compile(
    r"^.{0,6}(?:帮我|请|麻烦).{0,4}(?:发|转发|查|看一下|打开|关闭|设置|提醒|发送).{0,20}$"
)


# ---------------------------------------------------------------------------
# LLM Prompt 模板
# ---------------------------------------------------------------------------

TEAM_PLANNING_PROMPT = """你是灵雀的**首席任务架构师**。你的职责是分析用户请求，决定是否需要组建专业团队来并行处理。

## 用户请求
{request}

## 决策流程（严格按此判断）

### 第一步：判断是否可拆分
以下情况 **不拆分**（should_split = false）：
- 单一目标任务（写文章、查天气、发消息、翻译、总结）
- 任务间有依赖（B 需要 A 的结果才能开始）
- 简单枚举操作（同时发两条消息、设三个提醒）
- 用户意图模糊不清，无法明确拆分

以下情况 **适合拆分**：
- 多个独立对象的调研/对比/分析（3家竞品分析、多个平台价格对比）
- 并行的信息采集任务（分别搜索不同领域的信息）
- 不同专业领域的独立子目标（同时处理技术方案和市场分析）

### 第二步：设计角色（如果拆分）
为每个子任务设计一个**专业角色**，遵循以下原则：

**角色命名**: 用具体职能命名（如"电商数据分析师"、"竞品调研专员"），不要用泛泛的"助手A"
**角色人格**: 定义这个角色的工作风格和专业特质（如"擅长数据对比，注重用表格呈现结论，对数字敏感"）
**任务指令**: 必须是完整、独立、可直接执行的指令，包含：
  - 明确的目标（要做什么）
  - 具体的范围（做到什么程度）
  - 输出要求（以什么格式呈现结果）

## 可用技能类别
browser（浏览器/网页操作）, web（网络搜索）, search（搜索）, file（文件操作）,
code（代码执行）, memory（记忆）, feishu（飞书）, email（邮件）,
scheduler（定时任务）, general（通用）

## 输出格式（严格 JSON）
{{
  "should_split": true/false,
  "reason": "一句话说明拆分/不拆分的原因",
  "parallel": true/false,
  "merge_strategy": "summarize",
  "roles": [
    {{
      "name": "具体职能角色名",
      "persona": "2-3句话描述：专业背景、工作风格、擅长什么、注重什么",
      "task": "完整的任务指令，包含目标、范围和输出要求",
      "skills": ["browser", "web"],
      "max_loops": 6
    }}
  ]
}}

## 约束
- 角色最多 4 个
- 不拆分时 roles 为空数组 []
- max_loops: 按任务复杂度设定（简单 2-3, 中等 4-6, 复杂 6-8）
- 每个 task 必须自包含，不能引用其他角色的结果

只返回 JSON。"""


MERGE_PROMPT = """你是一位资深的**信息整合专家**。多位专业人员已经并行完成了各自的调研和执行工作，你需要将他们的成果整合为一份高质量的最终报告。

## 用户的原始请求
{request}

## 各专业人员的执行结果
{results}

## 执行过程中的实际操作
{side_effects}

## 整合原则

1. **统一视角**: 最终输出应该像一个人完成的一样自然连贯，不要提及"角色"、"子Agent"、"团队成员"等内部概念
2. **结构清晰**: 用合适的标题、分段、列表来组织信息，方便阅读
3. **信息完整**: 保留各方成果的关键数据和结论，不丢失重要细节
4. **诚实标注**: 如果某部分未完成或失败，坦诚说明，不编造结果
5. **实操可见**: 如果过程中创建了文件、发送了消息、保存了数据等实际操作，在回复中明确提及
6. **结论优先**: 先给出核心结论或摘要，再展开详细内容

## 输出格式
根据内容类型自动选择最佳格式：
- 对比类 → 表格
- 调研类 → 结构化报告（摘要 + 分项详述 + 结论）
- 执行类 → 操作清单 + 结果确认"""


# ---------------------------------------------------------------------------
# 副作用类型工具名
# ---------------------------------------------------------------------------

_SIDE_EFFECT_TOOLS = {
    "write_file", "create_file", "save_file", "file_write",
    "send_message", "send_feishu_message", "send_email",
    "send_file", "send_image",
    "browser_screenshot", "browser_save_cookies",
    "create_reminder", "create_scheduler",
    "run_code", "execute_code",
}


# ---------------------------------------------------------------------------
# 团队执行日志持久化
# ---------------------------------------------------------------------------

def _save_team_log(plan: TeamPlan, results: list[str], merged: str):
    """将团队执行记录保存到本地"""
    try:
        log_dir = os.path.join(
            os.environ.get("LINGQUE_WORKSPACE", os.path.expanduser("~/lingque")),
            "workspaces", "default", "team_logs",
        )
        os.makedirs(log_dir, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"team_{ts}.json")

        log_data = {
            "timestamp": ts,
            "request": plan.original_request,
            "parallel": plan.parallel,
            "roles": [],
            "merged_result_preview": merged[:500],
        }
        for task, result in zip(plan.subtasks, results):
            log_data["roles"].append({
                "name": task.role.name,
                "task": task.role.task,
                "status": task.status.value,
                "side_effects": task.side_effects,
                "elapsed": (
                    f"{(task.completed_at - task.created_at).total_seconds():.1f}s"
                    if task.completed_at and task.created_at else "N/A"
                ),
                "result_preview": result[:300],
            })

        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        logger.info(f"团队日志已保存: {log_file}")
    except Exception as e:
        logger.warning(f"团队日志保存失败: {e}")


# ---------------------------------------------------------------------------
# MultiAgentController
# ---------------------------------------------------------------------------

class MultiAgentController:
    """
    多 Agent 控制器

    负责:
    - 判断任务是否适合多 Agent 处理
    - 通过 LLM 规划角色和分工
    - 创建轻量子 Agent 并行执行
    - 收集和汇总结果
    - 管理取消信号
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
        self._cancel_event = asyncio.Event()
        self._blackboard = SharedBlackboard()
        self._last_team_log: Optional[dict] = None

    def set_progress_callback(self, callback):
        self._progress_callback = callback

    def cancel(self):
        """发送取消信号，中止正在执行的团队任务"""
        self._cancel_event.set()

    @staticmethod
    def should_use_team(user_input: str) -> bool:
        """判断用户输入是否适合使用多 Agent 团队模式"""
        if len(user_input) < 20:
            return False

        if _SIMPLE_TASK_PATTERNS.match(user_input):
            return False

        if _TEAM_KEYWORDS.search(user_input):
            return True

        score = 0
        for pat in _PARALLEL_PATTERNS:
            if re.search(pat, user_input):
                score += 1

        if score >= 1:
            return True

        items = re.findall(r"[、，,]\s*", user_input)
        if len(items) >= 3:
            multi_target_kw = ["竞品", "产品", "平台", "网站", "品牌", "渠道",
                               "公司", "方案", "价格", "行情"]
            if any(k in user_input for k in multi_target_kw):
                return True

        return False

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    async def process(self, request: str) -> Optional[str]:
        """
        处理用户请求：规划 → 组队 → 并行执行 → 汇总

        返回汇总结果字符串；如果不适合团队模式则返回 None（由主 Agent 降级处理）
        """
        self._cancel_event.clear()
        self._blackboard.clear()

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

        if self._cancel_event.is_set():
            self._current_team = None
            return "团队任务已取消。已完成的部分结果:\n" + "\n".join(
                f"- {t.role.name}: {t.result[:100]}" for t in plan.subtasks
                if t.status == SubTaskStatus.COMPLETED
            )

        merged = await self._merge_results(plan, results)

        _save_team_log(plan, results, merged)

        self._current_team = None
        return merged

    # ------------------------------------------------------------------
    # 规划
    # ------------------------------------------------------------------

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
                timeout=30,
            )

            text = (resp.content or "").strip()
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
                loops = rd.get("max_loops", 6)
                if not isinstance(loops, int) or loops < 2:
                    loops = 4
                elif loops > 10:
                    loops = 10

                role = AgentRole(
                    name=rd.get("name", "助手"),
                    persona=rd.get("persona", ""),
                    task=rd.get("task", ""),
                    skills=rd.get("skills", ["general"]),
                    max_loops=loops,
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
            logger.warning("团队规划超时(30s)")
            if self._progress_callback:
                try:
                    await self._progress_callback(0, 1, "团队规划超时，切换为单人模式", "running")
                except Exception:
                    pass
            return None
        except Exception as e:
            logger.warning(f"团队规划失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 子 Agent 执行
    # ------------------------------------------------------------------

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
        consecutive_errors = 0

        for step in range(role.max_loops):
            if self._cancel_event.is_set():
                logger.info(f"子Agent [{role.name}] 收到取消信号")
                break

            bb_content = await self._blackboard.read_all()
            if bb_content and step > 0:
                messages.append(Message(
                    role="user",
                    content=f"[团队共享信息]\n{bb_content}\n\n请继续你的任务。",
                ))

            resp = await self._llm_call_with_retry(role.name, messages, tools, system_prompt)
            if resp is None:
                consecutive_errors += 1
                if consecutive_errors >= 2:
                    logger.warning(f"子Agent [{role.name}] 连续失败 {consecutive_errors} 次，终止")
                    break
                continue
            consecutive_errors = 0

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
                    tc_id = tc.id or f"call_{uuid.uuid4().hex[:8]}"
                    tool_name = tc.name or "unknown"

                    logger.info(f"子Agent [{role.name}] 调用工具: {tool_name}")

                    if self._progress_callback:
                        try:
                            await self._progress_callback(
                                0, 0, f"{role.name}: {tool_name}", "running"
                            )
                        except Exception:
                            pass

                    try:
                        result_str = await asyncio.wait_for(
                            skill_registry.execute(tool_name, tc.arguments or {}),
                            timeout=60,
                        )
                    except asyncio.TimeoutError:
                        result_str = f"工具 {tool_name} 执行超时"
                    except Exception as e:
                        result_str = f"工具 {tool_name} 执行失败: {e}"

                    if tool_name in _SIDE_EFFECT_TOOLS:
                        effect_desc = f"{tool_name}: {str(result_str)[:120]}"
                        task.side_effects.append(effect_desc)
                        await self._blackboard.post(role.name, tool_name, str(result_str)[:200])

                    if len(result_str) > 8000:
                        result_str = result_str[:8000] + "\n... [结果已截断]"

                    messages.append(Message(
                        role="tool",
                        content=result_str,
                        name=tool_name,
                        tool_call_id=tc_id,
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

    async def _llm_call_with_retry(self, agent_name: str, messages, tools, system_prompt, max_retries: int = 1):
        """LLM 调用，失败自动重试一次"""
        for attempt in range(1 + max_retries):
            try:
                resp = await asyncio.wait_for(
                    self.llm_router.chat(
                        messages=messages,
                        tools=tools if tools else None,
                        system_prompt=system_prompt,
                    ),
                    timeout=60,
                )
                return resp
            except asyncio.TimeoutError:
                logger.warning(f"子Agent [{agent_name}] LLM 超时 (尝试 {attempt + 1})")
                if attempt < max_retries:
                    await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"子Agent [{agent_name}] LLM 异常 (尝试 {attempt + 1}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2)
        return None

    # ------------------------------------------------------------------
    # 执行编排
    # ------------------------------------------------------------------

    async def _execute_parallel(self, tasks: list[SubTask]) -> list[str]:
        """并行执行多个子 Agent"""
        semaphore = asyncio.Semaphore(self.max_parallel)

        async def run_one(task: SubTask) -> str:
            async with semaphore:
                if self._cancel_event.is_set():
                    task.status = SubTaskStatus.CANCELLED
                    return "(已取消)"

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

                    self._report_team_progress(task)
                    return result
                except asyncio.TimeoutError:
                    task.status = SubTaskStatus.FAILED
                    task.error = "执行超时"
                    task.completed_at = datetime.now()
                    logger.warning(f"子Agent [{task.role.name}] 超时 ({self.sub_agent_timeout}s)")
                    self._report_team_progress(task)
                    return f"({task.role.name} 执行超时)"
                except Exception as e:
                    task.status = SubTaskStatus.FAILED
                    task.error = str(e)
                    task.completed_at = datetime.now()
                    logger.warning(f"子Agent [{task.role.name}] 异常: {e}")
                    self._report_team_progress(task)
                    return f"({task.role.name} 执行失败: {e})"

        results = await asyncio.gather(*[run_one(t) for t in tasks])
        return list(results)

    async def _execute_sequential(self, tasks: list[SubTask]) -> list[str]:
        """串行执行多个子 Agent"""
        results = []
        for i, task in enumerate(tasks):
            if self._cancel_event.is_set():
                task.status = SubTaskStatus.CANCELLED
                results.append("(已取消)")
                continue

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
            self._report_team_progress(task)
        return results

    def _report_team_progress(self, completed_task: SubTask):
        """完成一个子任务后汇报整体进度"""
        if not self._progress_callback or not self._current_team:
            return
        done_count = sum(
            1 for t in self._current_team.subtasks
            if t.status in (SubTaskStatus.COMPLETED, SubTaskStatus.FAILED, SubTaskStatus.CANCELLED)
        )
        total = len(self._current_team.subtasks)
        status_icon = "✅" if completed_task.status == SubTaskStatus.COMPLETED else "❌"
        try:
            asyncio.ensure_future(self._progress_callback(
                done_count + 1, total + 1,
                f"{status_icon} {completed_task.role.name} 已完成", "running",
            ))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 结果汇总
    # ------------------------------------------------------------------

    async def _merge_results(self, plan: TeamPlan, results: list[str]) -> str:
        """用 LLM 汇总多个子 Agent 的结果"""
        from ..llm.base import Message

        results_text = ""
        side_effects_text = ""
        for task, result in zip(plan.subtasks, results):
            status_label = {
                SubTaskStatus.COMPLETED: "完成",
                SubTaskStatus.FAILED: "失败",
                SubTaskStatus.CANCELLED: "已取消",
            }.get(task.status, "未知")
            results_text += f"\n### {task.role.name}（{status_label}）\n{result}\n"

            if task.side_effects:
                for se in task.side_effects:
                    side_effects_text += f"- [{task.role.name}] {se}\n"

        if not side_effects_text:
            side_effects_text = "(无文件/消息等操作)"

        prompt = MERGE_PROMPT.format(
            request=plan.original_request,
            results=results_text,
            side_effects=side_effects_text,
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
            if side_effects_text != "(无文件/消息等操作)":
                output += f"\n### 执行操作\n{side_effects_text}\n"
            return output

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_status(self) -> str:
        """获取当前团队状态"""
        if not self._current_team:
            if self._last_team_log:
                return self._format_last_log()
            return "当前没有团队在执行任务。"

        plan = self._current_team
        lines = [f"**当前团队** ({len(plan.subtasks)} 个角色)\n"]
        for task in plan.subtasks:
            icon = {
                SubTaskStatus.PENDING: "⏳",
                SubTaskStatus.RUNNING: "🔄",
                SubTaskStatus.COMPLETED: "✅",
                SubTaskStatus.FAILED: "❌",
                SubTaskStatus.CANCELLED: "🚫",
            }.get(task.status, "❓")
            elapsed = ""
            if task.completed_at and task.created_at:
                secs = (task.completed_at - task.created_at).total_seconds()
                elapsed = f" ({secs:.0f}s)"
            lines.append(f"{icon} **{task.role.name}** - {task.role.task[:60]}{elapsed}")

        return "\n".join(lines)

    def _format_last_log(self) -> str:
        """格式化上一次团队日志"""
        log = self._last_team_log
        if not log:
            return "无历史团队记录。"
        lines = [f"**上次团队任务** ({log.get('timestamp', '未知时间')})\n"]
        lines.append(f"请求: {log.get('request', '未知')[:80]}\n")
        for r in log.get("roles", []):
            icon = "✅" if r.get("status") == "completed" else "❌"
            lines.append(f"{icon} **{r['name']}** ({r.get('elapsed', 'N/A')})")
        return "\n".join(lines)
