"""
🐦 灵雀 - Agent 核心工具调用循环
核心改进:
  1. 遇错保留上下文（不再清空会话）
  2. 自动注入合成 tool 结果修复消息格式
  3. 硬超时保护防止无限等待
  4. 运行时守卫由 Memory 层负责
  5. 死循环检测: 同一工具重复调用 3+ 次 → 强制换策略
"""

import asyncio
import contextvars
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from ..llm import LLMRouter, Message, ToolCall


@dataclass
class _PendingError:
    """待配对的工具错误"""
    tool_name: str
    error_msg: str
    args_summary: str
    timestamp: float


@dataclass
class _RequestState:
    """每个并发请求独立的运行时状态"""
    user_cancelled: bool = False
    recent_tool_calls: list = field(default_factory=list)
    recent_tool_results: list = field(default_factory=list)
    recent_tool_args: list = field(default_factory=list)
    stuck_intervention_count: int = 0
    consecutive_failures: dict = field(default_factory=dict)
    same_tool_streak: int = 0
    last_tool_name: str = ""
    stuck_type: str = ""
    # v3: 新增
    same_category_streak: int = 0       # 同类工具连续调用计数
    last_tool_category: str = ""        # 上一个工具的类别
    loops_without_text: int = 0         # 连续多少轮没有文字回复（全是工具调用）
    total_tool_calls: int = 0           # 本次请求总工具调用次数
    full_tool_history: list = field(default_factory=list)  # 完整历史（不清空）
    # 自我进化: 错误配对跟踪
    pending_errors: list = field(default_factory=list)     # 待配对的错误列表
    error_count: int = 0                                    # 本次请求工具错误计数
    tools_used_set: set = field(default_factory=set)        # 本次请求用过的工具集合


_req_state: contextvars.ContextVar[_RequestState] = contextvars.ContextVar("agent_req_state")
from ..skills import registry as skill_registry
from ..skills.self_improvement import auto_log_error
from .memory import Memory
from .context import ContextBuilder


def _get_learning_engine():
    """延迟获取 LearningEngine，避免循环导入"""
    try:
        from ..memory.learning_engine import get_learning_engine
        return get_learning_engine()
    except Exception:
        return None

logger = logging.getLogger("lobster.agent")

STUCK_LOOP_WINDOW = 8

# 容易陷入"调试循环"的工具（每次参数不同，但本质是重复尝试）
# 对这些工具只按名称计数，不管参数是否相同
LOOP_PRONE_TOOLS = {
    # 代码执行类
    "run_python", "sandbox_python", "sandbox_bash", "run_command",
    # 浏览器 JS 执行（每次调用独立上下文，LLM 不知道状态不共享）
    "browser_execute_js",
    # 查询类（LLM 喜欢反复用不同命令查询同一件事）
    "run_query",
}

# 工具类别映射（用于类别级别的循环检测）
_TOOL_CATEGORIES = {
    "browser_open": "browser", "browser_navigate": "browser",
    "browser_click": "browser", "browser_type": "browser",
    "browser_scroll": "browser", "browser_snapshot": "browser",
    "browser_back": "browser", "browser_forward": "browser",
    "browser_wait": "browser", "browser_wait_for": "browser",
    "browser_execute_js": "browser", "browser_check_state": "browser",
    "browser_interact": "browser", "browser_fill_form": "browser",
    "browser_tabs": "browser", "browser_tab_switch": "browser",
    "browser_close": "browser", "browser_history": "browser",
    "browser_screenshot_and_send": "browser",
    "browser_select": "browser", "browser_hover": "browser",
    "browser_press_key": "browser", "browser_rpa_config": "browser",
    "run_python": "code", "sandbox_python": "code",
    "sandbox_bash": "code", "run_command": "code",
    "run_query": "query",
    "fetch_webpage": "web", "web_search": "web",
    "read_file": "file", "write_file": "file", "list_directory": "file",
    "get_scheduler_status": "scheduler", "run_workflow": "workflow",
    "list_workflows": "workflow", "get_workflow_status": "workflow",
    "add_cron_task": "scheduler", "remove_cron_task": "scheduler",
    "list_cron_tasks": "scheduler",
}


def _get_tool_category(name: str) -> str:
    if name in _TOOL_CATEGORIES:
        return _TOOL_CATEGORIES[name]
    for prefix in ("browser_", "knowledge_", "mcp_"):
        if name.startswith(prefix):
            return prefix.rstrip("_")
    return "other"


class Agent:
    """
    AI Agent 核心引擎
    实现 ReAct 循环: Reason → Act → Observe → Reason → ...
    """

    def __init__(
        self,
        llm_router: LLMRouter,
        memory: Memory,
        max_loops: int = 25,
        require_confirmation: bool = True,
        agent_config=None,
    ):
        self.llm = llm_router
        self.memory = memory
        self.max_loops = max_loops
        self.require_confirmation = require_confirmation
        self.context_builder = ContextBuilder(skill_registry)

        self._agent_config = agent_config
        if agent_config:
            self.max_loops = agent_config.max_loops
            self._task_timeout = agent_config.task_timeout
            self._tool_timeout = agent_config.tool_timeout
            self._auto_continue = agent_config.auto_continue
        else:
            self._task_timeout = 600
            self._tool_timeout = 120
            self._auto_continue = 2

        self._confirm_callback = None
        self._progress_callback = None
        self._plan_callback = None

    def set_confirm_callback(self, callback):
        self._confirm_callback = callback

    def set_progress_callback(self, callback):
        self._progress_callback = callback

    def set_plan_callback(self, callback):
        self._plan_callback = callback

    async def _report_progress(self, step: int, total: int, skill_name: str, status: str = "running"):
        if self._progress_callback:
            try:
                await self._progress_callback(step, total, skill_name, status)
            except Exception as e:
                logger.warning(f"进度回调失败: {e}")

    @staticmethod
    def _rs() -> _RequestState:
        """获取当前请求的运行时状态"""
        return _req_state.get()

    def _track_tool_call(self, tool_call: ToolCall):
        """记录工具调用用于死循环检测（v3 增强）"""
        rs = self._rs()
        rs.total_tool_calls += 1
        
        # 追踪同一工具连续调用
        if tool_call.name == rs.last_tool_name:
            rs.same_tool_streak += 1
        else:
            rs.same_tool_streak = 1
            rs.last_tool_name = tool_call.name
        
        # v3: 追踪同类工具连续调用（browser_click → browser_snapshot → browser_click 都算 browser 类）
        category = _get_tool_category(tool_call.name)
        if category == rs.last_tool_category:
            rs.same_category_streak += 1
        else:
            rs.same_category_streak = 1
            rs.last_tool_category = category
        
        # 易循环工具：只按名称计数，忽略参数差异
        if tool_call.name in LOOP_PRONE_TOOLS:
            args_str = f"__loop_prone__{tool_call.name}"
        else:
            args_str = str(sorted(tool_call.arguments.items()))[:200]
        
        rs.recent_tool_calls.append((tool_call.name, args_str))
        if len(rs.recent_tool_calls) > STUCK_LOOP_WINDOW:
            rs.recent_tool_calls = rs.recent_tool_calls[-STUCK_LOOP_WINDOW:]
        
        rs.recent_tool_args.append((tool_call.name, tool_call.arguments))
        if len(rs.recent_tool_args) > STUCK_LOOP_WINDOW:
            rs.recent_tool_args = rs.recent_tool_args[-STUCK_LOOP_WINDOW:]
        
        # v3: 完整历史（不随干预清空，用于全局检测）
        rs.full_tool_history.append(tool_call.name)
    
    def _track_tool_result(self, tool_name: str, result: str):
        """记录工具执行结果用于无进展检测"""
        rs = self._rs()
        result_sig = (tool_name, hash(result[:500]), result[:100])
        rs.recent_tool_results.append(result_sig)
        if len(rs.recent_tool_results) > STUCK_LOOP_WINDOW:
            rs.recent_tool_results = rs.recent_tool_results[-STUCK_LOOP_WINDOW:]
    
    def _compute_args_similarity(self, args1: dict, args2: dict) -> float:
        """计算两个参数字典的相似度 (0-1)"""
        if not args1 or not args2:
            return 0.0
        
        # 提取所有关键词
        def extract_keywords(d: dict) -> set:
            words = set()
            for v in d.values():
                if isinstance(v, str):
                    # 分词
                    for w in str(v).lower().split():
                        if len(w) > 2:
                            words.add(w)
            return words
        
        words1 = extract_keywords(args1)
        words2 = extract_keywords(args2)
        
        if not words1 or not words2:
            return 0.0
        
        # Jaccard 相似度
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    def _detect_stuck_loop(self) -> tuple[str | None, str, str]:
        """
        检测死循环 (v3 全面增强)
        
        检测规则（按严重度排序）:
        1. 易循环工具连续调用 3 次（run_query/browser_execute_js/run_python 等）
        2. 任意工具连续调用 3 次（之前是4次，太宽松）
        3. 同一工具+相同参数重复 2 次
        4. 同类工具（如 browser_*）连续调用 6 次
        5. 无进展检测：连续 3 次结果相同
        6. 语义相似检测：参数相似度 > 0.6
        7. 全局超限：本次请求总工具调用超 20 次
        
        返回: (卡住的工具名, 原因描述, 卡住类型)
        """
        rs = self._rs()
        
        # 规则 2: 同一工具连续调用 N 次（易循环工具 2 次，普通工具 3 次）
        streak_threshold = 2 if rs.last_tool_name in LOOP_PRONE_TOOLS else 3
        if rs.same_tool_streak >= streak_threshold:
            rs.stuck_type = "tool_repeat"
            return rs.last_tool_name, f"连续调用 {rs.last_tool_name} {rs.same_tool_streak} 次", "tool_repeat"
        
        # 规则 4: 同类工具连续调用 6 次（跳过 "other" 这个 catch-all 分类）
        if rs.same_category_streak >= 6 and rs.last_tool_category != "other":
            cat = rs.last_tool_category
            rs.stuck_type = "category_loop"
            return rs.last_tool_name, f"连续调用 {cat} 类工具 {rs.same_category_streak} 次", "category_loop"
        
        # 规则 7 提前: 全局超限
        if rs.total_tool_calls >= 20 and rs.full_tool_history:
            top_tool = Counter(rs.full_tool_history).most_common(1)[0]
            if top_tool[1] >= 8:
                rs.stuck_type = "global_overuse"
                return top_tool[0], f"本次任务已调用 {rs.total_tool_calls} 次工具，其中 {top_tool[0]} 被调用 {top_tool[1]} 次", "global_overuse"
        
        if len(rs.recent_tool_calls) < 2:
            return None, "", ""
        
        recent = rs.recent_tool_calls[-STUCK_LOOP_WINDOW:]
        
        # 规则 1: 易循环工具连续调用 3 次
        consecutive_loop_prone = 0
        last_lp_tool = None
        for name, _ in reversed(recent):
            if name in LOOP_PRONE_TOOLS:
                if last_lp_tool is None or last_lp_tool == name:
                    consecutive_loop_prone += 1
                    last_lp_tool = name
                else:
                    break
            else:
                break
        
        if consecutive_loop_prone >= 2:
            rs.stuck_type = "code_loop"
            return last_lp_tool, f"连续调用 {last_lp_tool} {consecutive_loop_prone} 次，本质是重复尝试", "code_loop"
        
        # 规则 3: 同一工具+相同参数重复 2 次
        counter = Counter(recent)
        for (name, args), count in counter.most_common(1):
            if count >= 2:
                rs.stuck_type = "exact_repeat"
                return name, "重复调用相同工具和参数", "exact_repeat"
        
        # 规则 5: 无进展检测 - 连续 3 次输出结果相同
        if len(rs.recent_tool_results) >= 3:
            last_3 = rs.recent_tool_results[-3:]
            if all(r[1] == last_3[0][1] for r in last_3):
                rs.stuck_type = "no_progress"
                return last_3[0][0], "连续 3 次执行结果相同，没有进展", "no_progress"
        
        # 规则 6: 语义相似检测 - 同一工具参数高度相似（阈值从0.7降到0.6）
        if len(rs.recent_tool_args) >= 3:
            last_3_args = rs.recent_tool_args[-3:]
            if len(set(a[0] for a in last_3_args)) == 1:
                tool_name = last_3_args[0][0]
                sim_01 = self._compute_args_similarity(last_3_args[0][1], last_3_args[1][1])
                sim_12 = self._compute_args_similarity(last_3_args[1][1], last_3_args[2][1])
                avg_sim = (sim_01 + sim_12) / 2
                
                if avg_sim > 0.6:
                    rs.stuck_type = "semantic_similar"
                    return tool_name, f"连续 3 次调用参数高度相似 (相似度: {avg_sim:.0%})", "semantic_similar"
        
        return None, "", ""
    
    def _get_recovery_strategy(self, stuck_type: str, tool_name: str) -> str:
        """根据卡住类型返回针对性的恢复策略"""
        strategies = {
            "code_loop": (
                f"你在反复执行 {tool_name}，每次都没成功。必须立即停止重试！\n"
                "你应该：\n"
                "1. 直接告诉用户遇到了什么问题，不要再尝试\n"
                "2. 如果是环境问题（缺依赖、权限等），告诉用户需要手动解决\n"
                "3. 如果是逻辑问题，换一种完全不同的思路"
            ),
            "tool_repeat": (
                f"你已经连续调用 {tool_name} 多次了。不要再调用同一个工具！\n"
                "你应该：\n"
                "1. 停下来，总结你已经获得的信息\n"
                "2. 直接回答用户，即使信息不完整\n"
                "3. 如果确实需要更多信息，用一个完全不同的工具"
            ),
            "exact_repeat": (
                "你在重复完全相同的操作，这不会产生不同的结果。\n"
                "你应该：\n"
                "1. 阅读上次的返回结果\n"
                "2. 直接基于已有结果回答用户"
            ),
            "no_progress": (
                "多次执行但结果相同，说明当前方法无效。\n"
                "你应该：\n"
                "1. 承认当前方法行不通\n"
                "2. 告诉用户你尝试了什么、结果是什么\n"
                "3. 建议用户手动操作或提供更多信息"
            ),
            "category_loop": (
                f"你一直在使用 {_get_tool_category(tool_name)} 类工具反复操作，没有实质进展。\n"
                "你应该：\n"
                "1. 停止所有浏览器/代码/查询操作\n"
                "2. 总结你已获得的信息，直接回答用户\n"
                "3. 如果任务确实无法完成，明确告诉用户原因"
            ),
            "global_overuse": (
                f"本次任务已调用大量工具，说明任务可能超出当前能力范围。\n"
                "你必须：\n"
                "1. 立即停止调用工具\n"
                "2. 总结你目前完成了什么\n"
                "3. 告诉用户哪些部分完成了，哪些没完成"
            ),
            "semantic_similar": (
                "参数虽有变化但本质相似。建议：\n"
                "1. 可能是微调没有效果\n"
                "2. 需要从根本上换思路\n"
                "3. 向用户说明当前困难"
            ),
        }
        return strategies.get(stuck_type, "请尝试换一种方法解决问题。")

    _RECOVERY_HINTS: dict[str, str] = {
        "browser": "建议: 尝试 browser_snapshot 刷新元素编号，或换用 CSS/XPath 定位",
        "timeout": "建议: 稍后重试，或用 fetch_webpage 替代浏览器访问",
        "permission": "建议: 检查 ALLOWED_PATHS 配置，或换到允许的目录",
        "file": "建议: 确认文件路径是否正确，用 list_directory 先查看目录",
        "network": "建议: 检查网络连通性，稍后重试或换一个 URL",
    }

    def _enrich_error_result(self, tool_name: str, result: str) -> str:
        """对失败的工具结果追加策略建议（v3 增强）"""
        rs = self._rs()
        error_indicators = ["错误", "失败", "超时", "异常", "Error", "error",
                            "Timeout", "timeout", "not found", "拒绝"]
        is_error = any(ind in result for ind in error_indicators)
        if not is_error:
            rs.consecutive_failures.pop(tool_name, None)
            return result

        hint = ""
        if "browser" in tool_name.lower() or "browser" in result.lower():
            hint = self._RECOVERY_HINTS["browser"]
        elif "超时" in result or "timeout" in result.lower():
            hint = self._RECOVERY_HINTS["timeout"]
        elif "权限" in result or "permission" in result.lower() or "ALLOWED_PATHS" in result:
            hint = self._RECOVERY_HINTS["permission"]
        elif "file" in tool_name.lower() or "文件" in result:
            hint = self._RECOVERY_HINTS["file"]
        elif "网络" in result or "connect" in result.lower():
            hint = self._RECOVERY_HINTS["network"]

        rs.consecutive_failures[tool_name] = rs.consecutive_failures.get(tool_name, 0) + 1
        fail_count = rs.consecutive_failures[tool_name]
        if fail_count >= 2:
            hint += f"\n⚠️ 此工具已连续失败 {fail_count} 次！你必须停止重复此操作，换一种方法或直接告知用户。"
        
        # v3: 结合全局调用次数给出更强烈的提示
        if rs.same_tool_streak >= 2:
            hint += f"\n⚠️ 你已经连续调用 {tool_name} {rs.same_tool_streak} 次了，请尝试其他工具或直接回复用户。"

        if hint:
            result = f"{result}\n\n{hint}"
        return result

    @staticmethod
    def _is_complex_task(user_input: str) -> bool:
        """判断是否是复杂任务，需要先做规划"""
        if len(user_input) > 80:
            return True
        import re
        multi_step_patterns = [
            r"然后|接着|之后|同时|并且|以及|还要|再|另外",
            r"第[一二三四1-9]步",
            r"步骤|流程|批量|所有|全部",
            r"帮我.*(?:并|和|还).*",
        ]
        for pat in multi_step_patterns:
            if re.search(pat, user_input):
                return True
        action_keywords = ["安装", "部署", "配置", "搭建", "创建", "开发", "实现",
                           "迁移", "重构", "优化", "分析", "对比", "抓取"]
        hits = sum(1 for k in action_keywords if k in user_input)
        if hits >= 2:
            return True
        return False

    async def process_message(self, user_input: str, session_id: str = "default",
                              images: list[str] | None = None,
                              user_id: str = "default") -> str:
        """处理用户消息，带总超时保护"""
        self.memory.set_session(session_id)
        self.memory.set_user(user_id)
        await self.memory.flush_session_memories(self.llm)
        self.llm.reset_task_usage()
        timeout = self._task_timeout
        try:
            return await asyncio.wait_for(
                self._process_message_inner(user_input, images=images),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            minutes = timeout // 60
            logger.warning(f"任务总超时 ({timeout}s), session={session_id}")
            timeout_msg = f"⏰ 这个任务比较复杂，处理超过 {minutes} 分钟被中断了。你可以拆成更小的任务再试。"
            self.memory.add_message(Message(role="assistant", content=timeout_msg))
            return timeout_msg

    async def _process_message_inner(self, user_input: str,
                                     images: list[str] | None = None) -> str:
        """ReAct 循环主体"""
        session_id = self.memory._current_session
        self.memory.add_message(Message(role="user", content=user_input, images=images or []))

        # 上下文超长时 LLM 摘要压缩（token 阈值优先，消息条数兜底）
        from .memory import count_messages_tokens
        ctx_tokens = count_messages_tokens(self.memory.messages)
        if (ctx_tokens > self.memory.max_context_tokens
                or len(self.memory.messages) > self.memory.max_context_messages):
            logger.info(f"触发上下文压缩: {ctx_tokens} tokens, {len(self.memory.messages)} 条消息")
            await self.memory.compress_context(self.llm)

        _req_state.set(_RequestState())
        self._session_restarted = False

        # === 自我进化: 用户反馈检测 ===
        le = _get_learning_engine()
        if le and len(self.memory.messages) >= 3:
            prev_msgs = self.memory.messages[-4:]
            last_user = next((m.content for m in reversed(prev_msgs)
                              if m.role == "user" and m.content and m.content != user_input), "")
            last_asst = next((m.content for m in reversed(prev_msgs)
                              if m.role == "assistant" and m.content), "")
            le.detect_and_record_feedback(user_input, last_user, last_asst)

        long_term = self.memory.load_long_term()
        daily_notes = self.memory.load_recent_daily_notes(days=2)

        # 自动记忆召回：用用户消息搜索向量库，找到相关记忆注入上下文
        recalled = await self._auto_recall(user_input)

        recent_user_msgs = [
            m.content for m in self.memory.messages[-6:]
            if m.role == "user" and m.content
        ]
        system_prompt = self.context_builder.build_system_prompt(
            long_term, daily_notes, current_user=self.memory._current_user_id,
            recalled_memories=recalled,
            user_message=user_input,
            recent_messages=recent_user_msgs,
        )
        tools = skill_registry.select_tools_for_task(user_input)
        _used_tool_names: list[str] = []

        final_response = ""
        start_time = time.monotonic()
        consecutive_errors = 0

        if self._is_complex_task(user_input):
            try:
                plan_ctx = self.memory.get_context_messages()
                # P1 修复: 计划生成独立超时 20s，不吃掉主任务时间
                plan_resp = await asyncio.wait_for(
                    self.llm.chat(
                        messages=plan_ctx,
                        tools=None,
                        system_prompt=system_prompt + (
                            "\n\n[系统指令] 用户的任务较复杂。"
                            "请先用 1-3 个短步骤概述你的执行计划，不要调用工具。"
                            "只输出步骤编号和简短描述，不要输出代码、不要开始执行、不要解释。"
                            "格式示例:\n1. 第一步描述\n2. 第二步描述\n3. 第三步描述"
                        ),
                        session_id=session_id,
                    ),
                    timeout=20,
                )
                if plan_resp.content:
                    self.memory.add_message(Message(role="assistant", content=plan_resp.content))
                    if self._plan_callback:
                        try:
                            await self._plan_callback(plan_resp.content)
                        except Exception as e:
                            logger.warning(f"plan_callback 失败: {e}")
            except asyncio.TimeoutError:
                logger.warning("规划步骤超时(20s)，跳过")
            except Exception as e:
                logger.warning(f"规划步骤失败，跳过: {e}")

        # 长任务自动续航: 总步数上限 = max_loops * (1 + auto_continue)
        max_total_steps = self.max_loops * (1 + self._auto_continue)
        step = 0
        segment = 0              # 当前是第几段（0-based）
        segment_step = 0         # 当前段内第几步
        is_first_llm_call = True

        while step < max_total_steps:
            elapsed = time.monotonic() - start_time
            if elapsed > self._task_timeout:
                logger.warning(f"硬超时 ({self._task_timeout}s)")
                final_response = await self._generate_summary(system_prompt, elapsed, step)
                break

            # --- 自动续航检查点：到达段边界时压缩上下文并继续 ---
            if segment_step >= self.max_loops:
                segment += 1
                segment_step = 0
                logger.info(f"🔄 长任务自动续航 #{segment} | 已执行 {step} 步，压缩上下文继续")
                try:
                    await self.memory.compress_context(self.llm)
                except Exception as e:
                    logger.warning(f"续航压缩失败: {e}")
                self.memory.add_message(Message(
                    role="system",
                    content=(
                        f"[系统] 你已连续执行了 {step} 步工具调用。上下文已自动压缩，请继续完成任务。"
                        "不要重复已经完成的步骤，直接执行下一步。"
                        "如果任务已完成，直接给用户最终回复。"
                    ),
                    _is_intervention=True,
                ))
                if self._progress_callback:
                    try:
                        await self._progress_callback(
                            segment, self._auto_continue,
                            f"auto_continue_#{segment}", "running",
                        )
                    except Exception:
                        pass

            step += 1
            segment_step += 1
            logger.info(f"Agent 循环 #{step} (段{segment+1} 步{segment_step}, {elapsed:.0f}s)")

            # 每 4 轮检查一次，必要时压缩上下文
            if segment_step > 1 and segment_step % 4 == 0 and len(self.memory.messages) > 20:
                try:
                    from .memory import count_messages_tokens
                    mid_tokens = count_messages_tokens(self.memory.messages)
                    if mid_tokens > self.memory.max_context_tokens * 0.6:
                        logger.info(f"循环中压缩: {mid_tokens} tokens, {len(self.memory.messages)} 条消息")
                        await self.memory.compress_context(self.llm)
                except Exception as e:
                    logger.warning(f"循环中压缩失败: {e}")

            # 死循环检测（v3 全面增强）
            rs = self._rs()
            stuck_tool, stuck_reason, stuck_type = self._detect_stuck_loop()
            if stuck_tool:
                rs.stuck_intervention_count += 1
                logger.warning(f"检测到死循环: {stuck_tool} - {stuck_reason} [类型: {stuck_type}] (第 {rs.stuck_intervention_count} 次干预)")
                
                if rs.stuck_intervention_count >= 3:
                    final_response = await self._generate_summary(system_prompt, elapsed, step)
                    break
                else:
                    recovery_strategy = self._get_recovery_strategy(stuck_type, stuck_tool)
                    self.memory.add_message(Message(
                        role="system",
                        content=(
                            f"[SYSTEM OVERRIDE] Dead loop detected: {stuck_tool} ({stuck_reason}).\n"
                            f"Total tool calls: {rs.total_tool_calls}.\n"
                            f"Recovery: {recovery_strategy}\n"
                            "YOU MUST stop calling tools and respond to the user with text NOW. "
                            "Summarize what you've found and give a direct answer. "
                            "If you call any tool again, the system will terminate immediately."
                        ),
                        _is_intervention=True,
                    ))
                    tools = [t for t in tools if t["name"] != stuck_tool]
                    logger.info(f"已从工具列表移除 {stuck_tool}，剩余 {len(tools)} 个工具")
                    rs.recent_tool_calls.clear()
                    rs.recent_tool_results.clear()
                    rs.recent_tool_args.clear()

            context = self.memory.get_context_messages()
            try:
                response = await self.llm.chat(
                    messages=context,
                    tools=tools,
                    system_prompt=system_prompt,
                    session_id=session_id,
                )
                consecutive_errors = 0

                if is_first_llm_call:
                    is_first_llm_call = False
                    for msg in self.memory.messages:
                        if msg.images:
                            msg.images = []
            except Exception as e:
                error_msg = str(e)
                logger.error(f"LLM 调用失败: {error_msg}", exc_info=True)
                consecutive_errors += 1
                
                if "tool_calls" in error_msg and "tool messages" in error_msg:
                    logger.warning("检测到消息格式错误，修复消息历史")
                    self.memory.messages = self.memory._validate_message_pairs(self.memory.messages)
                    
                    if consecutive_errors >= 3 and not getattr(self, '_session_restarted', False):
                        logger.error("连续修复失败，精简重启（仅一次机会）")
                        self._session_restarted = True
                        last_user_msg = user_input
                        for msg in reversed(self.memory.messages):
                            if msg.role == "user":
                                last_user_msg = msg.content
                                break
                        self.memory.clear_session()
                        self.memory.add_message(Message(role="user", content=last_user_msg))
                    elif consecutive_errors >= 4:
                        final_response = "消息格式持续异常，请重新开始对话。"
                        self.memory.add_message(Message(role="assistant", content=final_response))
                        return final_response
                    continue
                else:
                    if consecutive_errors >= 2:
                        final_response = "处理请求时遇到问题，请稍后重试。"
                        self.memory.add_message(Message(role="assistant", content=final_response))
                        return final_response
                    await asyncio.sleep(min(consecutive_errors * 2, 5))
                    continue

            # 情况 A0: 工具调用参数被截断，注入重试指令
            if getattr(response, "tool_calls_truncated", False):
                logger.warning("工具调用参数被截断，注入重试指令")
                self.memory.add_message(Message(
                    role="assistant",
                    content=response.content or "让我重新组织一下...",
                ))
                self.memory.add_message(Message(
                    role="user",
                    content=(
                        "[系统] 你上次的工具调用参数太长被截断了，JSON 解析失败。"
                        "请将代码或内容拆分成更小的部分，分步执行。"
                        "不要一次传入过长的代码。"
                    ),
                ))
                continue

            # 情况 A: LLM 返回纯文本
            if not response.tool_calls:
                rs.loops_without_text = 0
                final_response = response.content
                self.memory.add_message(
                    Message(role="assistant", content=final_response)
                )
                break

            if (response.stop_reason == "stop"
                    and response.content and response.content.strip()
                    and len(response.content.strip()) > 20):
                logger.info("stop_reason=stop 且有实质内容，优先采纳文本回复，跳过工具调用")
                rs.loops_without_text = 0
                final_response = response.content
                self.memory.add_message(
                    Message(role="assistant", content=final_response)
                )
                break

            if response.stop_reason == "length":
                logger.warning("stop_reason=length，上下文过长，触发压缩")
                try:
                    await self.memory.compress_context(self.llm)
                except Exception as e:
                    logger.warning(f"length 压缩失败: {e}")
                continue

            # 追踪连续无文字回复的轮次
            rs.loops_without_text += 1
            if rs.loops_without_text >= 8:
                self.memory.add_message(Message(
                    role="system",
                    content=(
                        f"[SYSTEM OVERRIDE] You have executed {rs.loops_without_text} consecutive tool calls "
                        f"({rs.total_tool_calls} total) without replying to the user.\n"
                        "STOP calling tools. Summarize the information you have and respond to the user NOW."
                    ),
                    _is_intervention=True,
                ))
                rs.loops_without_text = 0

            # 情况 B: LLM 请求调用工具
            tool_results = []
            total_tools = len(response.tool_calls)
            
            for idx, tool_call in enumerate(response.tool_calls, 1):
                if self._rs().user_cancelled:
                    tool_results.append((tool_call, "[用户已取消] 操作被跳过"))
                    continue

                self._track_tool_call(tool_call)
                rs.tools_used_set.add(tool_call.name)
                await self._report_progress(idx, total_tools, tool_call.name, "running")
                
                tool_timeout = self._get_tool_timeout(tool_call.name)
                _t0 = time.monotonic()
                _tool_ok = True
                try:
                    result = await asyncio.wait_for(
                        self._execute_tool(tool_call),
                        timeout=tool_timeout,
                    )
                except asyncio.TimeoutError:
                    _tool_ok = False
                    logger.error(f"工具 {tool_call.name} 执行超时 ({tool_timeout}s)")
                    result = f"工具执行超时 ({tool_timeout}s): {tool_call.name}"
                    auto_log_error(tool_call.name, f"超时 {tool_timeout}s",
                                   str(tool_call.arguments)[:100])
                except Exception as e:
                    _tool_ok = False
                    logger.error(f"工具 {tool_call.name} 执行异常: {e}", exc_info=True)
                    result = f"工具执行异常: {e}"
                    auto_log_error(tool_call.name, str(e)[:300],
                                   str(tool_call.arguments)[:100])
                _elapsed_ms = int((time.monotonic() - _t0) * 1000)

                result = self._enrich_error_result(tool_call.name, result)
                self._track_tool_result(tool_call.name, result)
                tool_results.append((tool_call, result))

                # === 自我进化: 工具统计 + 错误修复配对 ===
                le = _get_learning_engine()
                if le:
                    le.record_tool_execution(tool_call.name, _tool_ok, _elapsed_ms)
                    if not _tool_ok:
                        rs.error_count += 1
                        rs.pending_errors.append(_PendingError(
                            tool_name=tool_call.name,
                            error_msg=result[:200],
                            args_summary=str(tool_call.arguments)[:100],
                            timestamp=time.monotonic(),
                        ))
                        fix_hint = le.recall_fix_for_error(result[:200])
                        if fix_hint:
                            self.memory.add_message(Message(
                                role="user", content=fix_hint,
                            ))
                    elif rs.pending_errors:
                        pe = rs.pending_errors.pop(0)
                        le.record_error_fix(
                            error_tool=pe.tool_name,
                            error_msg=pe.error_msg,
                            fix_tool=tool_call.name,
                            fix_args_summary=str(tool_call.arguments)[:100],
                        )

            # 原子性添加
            self.memory.add_message(
                Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                    reasoning_content=response.reasoning_content,
                )
            )
            MAX_TOOL_RESULT_CHARS = 3000
            from .memory import redact_secrets as _redact
            for tool_call, result in tool_results:
                content = _redact(result)
                if len(content) > MAX_TOOL_RESULT_CHARS:
                    content = content[:MAX_TOOL_RESULT_CHARS] + f"\n... [结果已截断，原始 {len(result)} 字符]"
                self.memory.add_message(
                    Message(
                        role="tool",
                        content=content,
                        name=tool_call.name,
                        tool_call_id=tool_call.id,
                    )
                )

            new_names = [tc.name for tc, _ in tool_results if tc.name not in _used_tool_names]
            if new_names:
                _used_tool_names.extend(new_names)
                tools = skill_registry.select_tools_for_task(user_input, _used_tool_names)

            # 用户取消 → 立即终止 Agent 循环
            if self._rs().user_cancelled:
                final_response = "好的，操作已取消。"
                self.memory.add_message(Message(role="assistant", content=final_response))
                logger.info("用户取消操作，Agent 循环终止")
                break
        else:
            # while 循环正常结束（达到 max_total_steps）
            elapsed = time.monotonic() - start_time
            final_response = await self._generate_summary(system_prompt, elapsed, step)

        # === 自我进化: 任务后反思 ===
        await self._post_task_reflect(user_input, system_prompt)

        # === 最终输出脱敏: 防止 LLM 回复中泄露密钥 ===
        from .memory import redact_secrets
        final_response = redact_secrets(final_response)

        logger.info(f"Agent 回复完成 | {self.llm.get_usage_summary()}")
        return final_response

    async def _generate_summary(self, system_prompt: str, elapsed: float, steps: int) -> str:
        """让 LLM 总结当前进度"""
        logger.warning(f"请求总结 ({steps} 步, {elapsed:.0f}s)")
        
        self.memory.add_message(
            Message(
                role="user",
                content=(
                    f"[系统] 你已经执行了 {steps} 个步骤 ({elapsed:.0f} 秒)，任务处理已到达上限。"
                    "请总结目前的工作成果：\n"
                    "1. 已完成的部分\n"
                    "2. 尚未完成的部分（如有）\n"
                    "3. 如果还有未完成的工作，告诉用户发送「继续」即可接着完成\n"
                    "不要建议用户手动操作，不要再调用工具，直接回复。"
                ),
            )
        )
        
        try:
            context = self.memory.get_context_messages()
            resp = await self.llm.chat(
                messages=context,
                tools=None,
                system_prompt=system_prompt,
                session_id=self.memory._current_session,
            )
            result = resp.content
        except Exception as e:
            logger.error(f"获取总结失败: {e}")
            result = (
                f"已执行 {steps} 个步骤，任务可能未完全完成。"
                "如需继续，请告诉我接下来做什么。"
            )
        
        self.memory.add_message(Message(role="assistant", content=result))
        return result

    async def _post_task_reflect(self, user_input: str, system_prompt: str) -> None:
        """任务结束后自动反思：简单任务零 token，出错任务用 LLM 提取经验"""
        try:
            le = _get_learning_engine()
            if le is None:
                return
            rs = self._rs()
            tools_used = list(rs.tools_used_set)

            if rs.total_tool_calls < 3 and rs.error_count == 0:
                return

            task_summary = user_input[:150]

            if rs.error_count == 0 and rs.stuck_intervention_count == 0:
                le.record_reflection(task_summary, tools_used, 0)
                return

            try:
                reflection_prompt = (
                    f"任务: {task_summary}\n"
                    f"工具调用: {rs.total_tool_calls} 次，错误: {rs.error_count} 次\n"
                    f"使用工具: {', '.join(tools_used[:8])}\n"
                    f"死循环干预: {rs.stuck_intervention_count} 次\n\n"
                    "用 1-2 句话总结这次任务的经验教训（什么做法有效，什么应该避免）。"
                    "只输出经验，不要客套。"
                )
                resp = await self.llm.chat(
                    messages=[Message(role="user", content=reflection_prompt)],
                    system_prompt="你是经验总结助手。只输出精炼的经验教训。",
                    temperature=0.3,
                )
                lesson = (resp.content or "").strip()[:300]
                le.record_reflection(task_summary, tools_used, rs.error_count, lesson)
            except Exception as e:
                logger.debug(f"LLM 反思失败: {e}")
                le.record_reflection(task_summary, tools_used, rs.error_count)
        except Exception as e:
            logger.debug(f"任务反思异常: {e}")

    async def _auto_recall(self, user_input: str) -> str:
        """自动记忆召回：用用户消息搜索向量库，找到相关记忆注入上下文"""
        if not user_input or len(user_input) < 4:
            return ""
        # 跳过命令
        if user_input.startswith("/"):
            return ""
        try:
            from ..memory.vector_store import get_vector_memory
            memory = get_vector_memory()
            if memory is None:
                return ""
            if memory.count() == 0:
                return ""
            results = memory.search(user_input, top_k=3, min_score=0.3)
            if not results:
                return ""
            lines = []
            for item in results:
                score_pct = int(item.score * 100)
                lines.append(f"- [{score_pct}%] {item.content[:200]}")
            recalled = "\n".join(lines)
            from .memory import redact_secrets
            recalled = redact_secrets(recalled)
            logger.info(f"自动召回 {len(results)} 条相关记忆")
            return recalled
        except Exception as e:
            logger.warning(f"自动记忆召回失败: {e}")
            return ""

    def _get_tool_timeout(self, tool_name: str) -> int:
        """获取工具超时时间：配置覆盖 > 默认"""
        if self._agent_config:
            return self._agent_config.get_tool_timeout(tool_name)
        return self._tool_timeout

    async def _execute_tool(self, tool_call: ToolCall) -> str:
        """执行单个工具调用，含安全检查"""
        skill = skill_registry.get(tool_call.name)
        if not skill:
            return f"未知工具: {tool_call.name}"

        need_confirmation = (
            self.require_confirmation
            and self._confirm_callback
            and skill.risk_level == "high"
            and self._is_destructive(tool_call)
        )

        if need_confirmation:
            desc = self._format_confirmation_desc(skill, tool_call)
            confirmed = await self._confirm_callback(desc)
            if not confirmed:
                self._rs().user_cancelled = True
                logger.info(f"用户取消操作，Agent 将停止: {tool_call.name}")
                return f"[用户已取消] 操作被用户拒绝: {tool_call.name}"

        return await skill_registry.execute(tool_call.name, tool_call.arguments)

    _DESTRUCTIVE_KEYWORDS = re.compile(
        r'\b(rm|rmdir|del|unlink|shred|truncate)\b'
    )

    def _is_destructive(self, tool_call: ToolCall) -> bool:
        """只有真正破坏性的操作才需要用户确认，普通系统命令直接放行"""
        if tool_call.name == "run_command":
            cmd = tool_call.arguments.get("command", "")
            return bool(self._DESTRUCTIVE_KEYWORDS.search(cmd))
        return True

    def _format_confirmation_desc(self, skill, tool_call: ToolCall) -> str:
        """格式化确认描述，让用户更容易理解"""
        name = tool_call.name
        args = tool_call.arguments

        # 技能友好名称映射
        friendly_names = {
            "run_python": "执行 Python 代码",
            "run_command": "执行系统命令",
            "write_file": "写入文件",
            "send_email": "发送邮件",
            "create_calendar_event": "创建日历事件",
            "submit_background_task": "提交后台任务",
            "generate_skill": "生成新技能",
            "memory_clear": "清空记忆库",
            "sandbox_python": "在沙箱中执行 Python",
            "sandbox_bash": "在沙箱中执行命令",
        }

        friendly_name = friendly_names.get(name, skill.description[:30])

        if name == "run_python":
            code = args.get("code", "")
            lines = code.strip().split("\n")
            preview = lines[0][:50] + "..." if len(lines[0]) > 50 else lines[0]
            return f"**{friendly_name}**\n\n📝 代码预览: `{preview}`\n📊 共 {len(lines)} 行代码"

        elif name == "run_command":
            cmd = args.get("command", "")
            return f"**{friendly_name}**\n\n💻 命令: `{cmd[:100]}`"

        elif name == "write_file":
            path = args.get("path", "")
            content = args.get("content", "")
            return f"**{friendly_name}**\n\n📁 路径: `{path}`\n📝 内容长度: {len(content)} 字符"

        elif name == "send_email":
            to = args.get("to", "")
            subject = args.get("subject", "")
            return f"**{friendly_name}**\n\n📧 收件人: {to}\n📋 主题: {subject}"

        elif name == "memory_clear":
            return f"**{friendly_name}**\n\n⚠️ 这将清空所有已保存的记忆数据！"

        else:
            summary = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(args.items())[:3])
            return f"**{friendly_name}**\n\n📋 参数: {summary}"

    def get_status(self, session_id: str = "default") -> str:
        mem_stats = self.memory.get_stats()
        active_model = self.llm.get_active_provider(session_id)
        model_id = self.llm.providers[active_model].model if active_model in self.llm.providers else "?"
        return (
            f"🐦 Agent 状态\n"
            f"当前模型: {active_model} ({model_id})\n"
            f"会话消息数: {mem_stats['session_messages']}\n"
            f"日志文件数: {mem_stats['log_files']}\n"
            f"{self.llm.get_usage_summary()}"
        )
