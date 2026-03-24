"""
🐦 上下文构建器
动态组装 system prompt，注入记忆、技能列表、运行环境信息
"""

import json
import os
import platform
import re
from collections import defaultdict
from datetime import datetime
from ..skills.registry import SkillRegistry


_BROWSER_KEYWORDS = re.compile(
    r"打开|网页|浏览器|登录|闲鱼|淘宝|goofish|taobao|browser|截图|cookie|抓取|爬|网站|链接|url",
    re.IGNORECASE,
)

_BROWSER_GUIDE_FULL = """\
## 浏览器自动化指南
浏览器使用真实 Chrome/Edge（CDP 模式），能正常访问淘宝、闲鱼等有反爬检测的网站。

### 核心流程
1. **打开页面**: `browser_open(url)` → 自动加载 Cookie，返回元素快照（[e1] [e2]...编号）
2. **操作元素**: `browser_click("e3")` 点击、`browser_type("e5", "内容")` 输入
3. **刷新快照**: 页面变化后 `browser_snapshot` 重新扫描（操作失败时系统会自动重试）
4. **保存登录**: `browser_save_cookies` 保存，下次访问自动登录

### 重要规则
- **元素编号会过期**: 导航/内容变化后旧编号失效（系统会自动重新扫描重试一次）
- **先看后操作**: 先看快照有哪些元素，再决定操作哪个
- **下拉框直接用 browser_select**: 看到 combobox/select 元素直接调用，不要用 click、execute_js 或 analyze_page 绕路
- **表单用 fill_form**: 多个输入框一次填完效率更高，自动识别下拉框
- **等待加载**: 用 `browser_wait` 等待特定元素或文字出现
- **多标签页**: `browser_tab_new` 开新标签，`browser_tabs` 查看所有

### 操作工具选择
- **点击**: `browser_click("e3")` — 按钮、链接、标签页
- **输入**: `browser_type("e5", "内容")` — 输入框、搜索框
- **下拉框**: `browser_select("e4", value="选项文本")` — ⚠️ **下拉框/select/combobox 必须用这个**，自动展开+滚动查找+选中
- **容器内滚动**: `browser_scroll_element("e6", direction="down")` — 在可滚动区域（下拉面板、列表）内滚动
- **悬停**: `browser_hover("e2")` — 触发悬停菜单、tooltip
- **拖拽/滑块**: `browser_drag("e5", x_offset=280)` — 滑块验证码、拖放操作，自动模拟人类轨迹
- **键盘**: `browser_press_key("Escape")` — 关闭弹窗；`ArrowDown/ArrowUp` 在下拉框中选择
- **表单**: `browser_fill_form(fields=[...])` — 一次填完多个字段

### 定位方式（4种）
- **元素编号**: e1, e2... → 来自快照，最常用
- **CSS 选择器**: .class, #id, div[attr] → 精确定位
- **XPath**: //div[@class="item"] → 复杂结构
- **文字匹配**: 直接传 "登录"、"下一页" → 最简单

### 典型场景
- **搜索**: open → type(搜索框, 关键词, press_enter=true) → 看结果
- **登录**: open → fill_form([用户名, 密码], submit_ref=登录按钮) → save_cookies
- **弹窗登录**: 快照会标注弹窗内元素并优先显示，直接操作弹窗内的编号即可
- **下拉框**: browser_select("e4", value="北京") — 自动展开、滚动查找、选中（不要用 browser_click 操作下拉框！）
- **滑块验证码**: `browser_solve_slider()` — 自动搜索并完成滑块验证（不需要元素编号，支持 iframe）
- **关闭弹窗**: browser_press_key("Escape") — 关闭弹窗/遮罩
- **抓数据**: 见下方数据抓取指南

### 数据抓取指南
1. **API 数据抓取**（最优，电商首选）:
   - browser_network_start(url_filter="api") → 操作页面(搜索/翻页/滚动) → browser_network_get(content_type="json")
   - 淘宝: url_filter="mtop" | 闲鱼: url_filter="mtop.idle" | 京东: url_filter="api.m.jd" | 抖音: url_filter="api"
2. **自动翻页采集**（多页数据一句话搞定，强烈推荐）:
   - `browser_collect_pages(item_selector="li[data-sku]", fields={"title":".p-name","price":".p-price"}, max_pages=5)`
   - 自动提取当前页 → 点击"下一页" → 重复，直到采完所有页或达到上限
   - 下一页按钮自动检测（下一页/Next/»），也可手动指定 next_button
   - 自动去重 + 容错（连续2页无新数据自动停止）
3. **智能相似元素采集**（单页深度提取）:
   - `browser_analyze_structure` → 识别页面列表/表单/表格区域
   - `browser_find_similar(selector=".product:first-child")` → 自动找到所有同类商品卡片并提取数据
   - `browser_extract_list(container="#list", item_selector=".item", fields={"title":".name","price":".price","img":"img"})` → 精准字段级提取
4. **滚动采集**（无限滚动/懒加载）:
   - browser_scroll_collect(selector=".item-card", sub_selectors={"title":".title","price":".price"}, scroll_times=5)
5. **HTML 提取**（通用）:
   - browser_extract(selector=".goods-item") / browser_extract_table() / browser_extract_links()
6. **操作策略**: 先用网络监听看有没有 API JSON → 有就用 → 多页数据直接 collect_pages 一句话采完 → 单页深度用 find_similar/extract_list → 无限滚动用 scroll_collect

### 元素操作稳定性
- 页面变化后元素编号失效时，系统会自动三层恢复：选择器直接命中 → 语义模糊匹配 → 位置匹配
- DOM 发生变化时扩展会主动通知，操作前自动刷新快照
- 操作失败不要直接重试，先 browser_snapshot 看最新状态

### 电商平台常用选择器参考
- **淘宝**: 商品卡片 `[data-spm] a`, 标题 `.title`, 价格 `.price`, 翻页 `.next`
- **闲鱼**: 商品卡片 `.item-card`, `.feed-item`, 标题 `.title`, 价格 `.price`
- **京东**: 列表 `#J_goodsList li[data-sku]`, 标题 `.p-name`, 价格 `.p-price`, 翻页 `.pn-next`
- **抖音**: 视频/商品卡片 `[data-e2e]`, 用户信息 `.author-card`
- **通用**: 先 `browser_analyze_structure` 自动识别 → 多页就 `browser_collect_pages` → 单页就 `browser_find_similar`"""

_BROWSER_GUIDE_SHORT = "浏览器自动化可用（browser_open 等技能），需要操作网页时可直接使用。"


_SKILL_CATEGORY_MAP = {
    "general": "通用",
    "browser": "浏览器",
    "file": "文件",
    "code": "代码执行",
    "memory": "记忆",
    "feishu": "飞书",
    "email": "邮件",
    "calendar": "日历",
    "reminder": "提醒",
    "search": "搜索",
    "web": "网页",
    "system": "系统",
    "workflow": "工作流",
    "plugin": "插件",
    "mcp": "MCP",
    "self_improvement": "自我改进",
}


SYSTEM_PROMPT_TEMPLATE = """你是 LingQue 🐦，一个运行在用户私人设备上的 AI 助手。

## 你的身份
- 你是用户的私人 AI 助手，不只是工具，更像一个可靠的搭档和伙伴
- 你会记住用户的偏好和习惯，随着相处越来越了解用户
- 你跑在用户自己的机器上，所有数据都在本地，安全可控
- 你可以操作文件、浏览网页、执行代码、管理日程

{user_profile_section}

## 当前环境
- 时间: {current_time}
- 系统: {os_info}
- 已加载技能: {skill_count} 个
- 当前用户: {current_user}

## 可用技能（按类别）
{skills_summary}

## 长期记忆
{long_term_memory}

## 最近动态
{daily_notes}

## 相关记忆（自动召回）
{recalled_memories}

{knowledge_context}

{recent_learnings}

{browser_guide}

{milestone_greeting}

## 行为准则
1. 收到复杂任务时，先简要说明计划（1-3步），再开始执行
2. 优先使用工具完成任务，而不是只给建议
3. 操作文件前先确认路径，避免误操作
4. 高风险操作（删除文件、发邮件）前要跟用户确认
5. 遇到不确定的情况，问用户而不是猜测
6. 保持简洁，不废话
7. 如果发现值得记住的用户偏好或重要信息，主动记录到长期记忆
8. 注意用户的情绪状态，适当给予共情和鼓励；用户沮丧时耐心安慰，成功时一起庆祝
9. **绝对禁止**在回复中输出任何 API Key、密码、Token、Secret 等敏感信息。即使用户要求查看，也只能显示前4位+掩码（如 sk-ce****）。记忆中如包含密钥，引用时必须脱敏
10. 用户提供 API Key / Token / Secret 时，必须使用 `save_credential` 保存到凭证保险箱，**禁止**写入 .env 文件或长期记忆。需要使用时用 `get_credential` 读取，或直接通过 os.getenv() 获取（启动时已自动注入环境变量）

## 严格执行限制（防止死循环）
- 同一个工具最多连续调用 2 次，如果 2 次都没成功，必须换方法或直接回复用户
- run_query 查询系统信息时，用 && 合并成一条命令，不要分多次调用
- browser_execute_js 每次调用是独立上下文，变量不共享，所有逻辑必须写在一段代码中
- 如果工具返回错误，不要用相同参数重试，要分析错误原因后换方法
- 遇到无法解决的问题，直接告诉用户情况，不要无限重试
"""


SUB_AGENT_PROMPT_TEMPLATE = """# 角色身份

你是 **{role_name}**，灵雀 AI 团队中的专业成员。你被选中是因为你的专长与当前任务高度匹配。

**你的人格特质**: {persona}

---

## 核心任务

{task}

---

## 思考框架（每一步执行前，按此顺序思考）

1. **目标确认**: 当前子目标是什么？离最终目标还差什么？
2. **信息评估**: 手头已有的信息够不够？缺什么？
3. **行动选择**: 最高效的下一步操作是什么？（优先用工具获取真实数据，而非凭空推测）
4. **风险预判**: 这步操作可能失败吗？失败了怎么补救？

## 工具使用策略

- **行动优先**: 能用工具获取真实信息的，绝不靠猜测编造
- **精准调用**: 选最匹配的工具，参数填写完整准确
- **结果验证**: 工具返回后，判断结果是否满足需要，不满足再调整
- **避免循环**: 同一工具同一参数最多调用 2 次，无效则换方案
- **异常处理**: 工具失败时，记录原因，尝试替代方案或如实汇报

## 可用技能

{skills_summary}

## 当前环境

- 时间: {current_time}
- 系统: {os_info}

## 输出质量标准

- **准确**: 数据和结论有工具结果支撑，不编造不臆测
- **完整**: 覆盖任务要求的所有要点，不遗漏
- **简洁**: 去掉冗余，结果直接呈现核心信息
- **可用**: 如果产出了文件、链接、数据，明确指出位置和内容

## 红线

- **绝对禁止**输出 API Key、密码、Token 等敏感信息
- **不要**偏离你的任务去处理无关事项
- **不要**在无法完成时编造虚假结果，如实说明即可
"""


class ContextBuilder:
    """构建发送给 LLM 的 system prompt"""

    def __init__(self, skill_registry: SkillRegistry):
        self.skill_registry = skill_registry

    MAX_MEMORY_CHARS = 4000
    MAX_NOTES_CHARS = 1500
    MAX_RECALL_CHARS = 1000
    MAX_LEARNINGS_CHARS = 1000
    MAX_SKILLS_CHARS = 2000

    @staticmethod
    def _should_inject_browser_guide(user_message: str, recent_messages: list[str] | None = None) -> bool:
        """判断是否需要注入完整浏览器指南"""
        texts = [user_message] + (recent_messages or [])
        return any(_BROWSER_KEYWORDS.search(t) for t in texts if t)

    def _build_skills_summary(self) -> str:
        """按类别分组，只显示名称列表"""
        skills = self.skill_registry.list_all()
        if not skills:
            return "(无可用技能)"

        groups: dict[str, list[str]] = defaultdict(list)
        for s in skills:
            groups[s.category].append(s.name)

        lines = []
        for cat, names in sorted(groups.items()):
            label = _SKILL_CATEGORY_MAP.get(cat, cat)
            lines.append(f"- **{label}**: {', '.join(sorted(names))}")
        result = "\n".join(lines)
        if len(result) > self.MAX_SKILLS_CHARS:
            result = result[:self.MAX_SKILLS_CHARS] + f"\n... (共 {len(skills)} 个技能)"
        return result

    @staticmethod
    def _load_recent_learnings(user_message: str = "") -> str:
        """从 LearningEngine 语义召回相关经验（fallback 到 JSONL 最近 5 条）"""
        try:
            from ..memory.learning_engine import get_learning_engine
            le = get_learning_engine()
            if le:
                result = le.recall_relevant_learnings(user_message) if user_message else le._fallback_recent_learnings()
                if result and len(result) > ContextBuilder.MAX_LEARNINGS_CHARS:
                    result = result[:ContextBuilder.MAX_LEARNINGS_CHARS] + "\n..."
                return result
        except Exception:
            pass
        return ""

    @staticmethod
    def _build_tool_insights() -> str:
        """从 LearningEngine 获取工具使用洞察"""
        try:
            from ..memory.learning_engine import get_learning_engine
            le = get_learning_engine()
            if le:
                return le.get_tool_insights()
        except Exception:
            pass
        return ""

    @staticmethod
    def _build_user_profile_section(current_user: str) -> str:
        try:
            from ..memory.user_profile import get_profile_manager
            pm = get_profile_manager()
            if pm and current_user and current_user != "default":
                return pm.build_context_summary(current_user)
        except Exception:
            pass
        return ""

    @staticmethod
    def _build_milestone_greeting(current_user: str) -> str:
        try:
            from ..memory.user_profile import get_profile_manager
            pm = get_profile_manager()
            if pm and current_user and current_user != "default":
                return pm.build_milestone_greeting(current_user)
        except Exception:
            pass
        return ""

    @staticmethod
    def _build_knowledge_context(user_message: str) -> str:
        if not user_message:
            return ""
        try:
            from ..memory.knowledge_graph import get_knowledge_graph
            kg = get_knowledge_graph()
            if kg:
                ctx = kg.get_context_for_query(user_message, max_entities=3)
                if ctx:
                    return f"## 相关知识（知识图谱）\n{ctx}"
        except Exception:
            pass
        return ""

    def build_sub_agent_prompt(self, role) -> str:
        """为子 Agent 构建精简的角色 system prompt"""
        skills = self.skill_registry.get_skills_by_categories(role.skills)
        if skills:
            groups: dict[str, list[str]] = defaultdict(list)
            for s in skills:
                groups[s.category].append(s.name)
            lines = []
            for cat, names in sorted(groups.items()):
                label = _SKILL_CATEGORY_MAP.get(cat, cat)
                lines.append(f"- **{label}**: {', '.join(sorted(names))}")
            skills_summary = "\n".join(lines)
        else:
            skills_summary = "(无专属技能，可直接回答)"

        return SUB_AGENT_PROMPT_TEMPLATE.format(
            role_name=role.name,
            persona=role.persona or "高效、专业地完成分配的任务",
            task=role.task,
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            os_info=f"{platform.system()} {platform.release()}",
            skills_summary=skills_summary,
        )

    def build_system_prompt(self, long_term_memory: str = "", daily_notes: str = "",
                            current_user: str = "default",
                            recalled_memories: str = "",
                            user_message: str = "",
                            recent_messages: list[str] | None = None) -> str:
        skills_summary = self._build_skills_summary()

        from .memory import redact_secrets
        if long_term_memory:
            long_term_memory = redact_secrets(long_term_memory)
        if daily_notes:
            daily_notes = redact_secrets(daily_notes)
        if long_term_memory and len(long_term_memory) > self.MAX_MEMORY_CHARS:
            long_term_memory = long_term_memory[:self.MAX_MEMORY_CHARS] + "\n... [记忆过长已截断]"
        if daily_notes and len(daily_notes) > self.MAX_NOTES_CHARS:
            daily_notes = daily_notes[-self.MAX_NOTES_CHARS:] + "\n... [笔记过长已截断]"
        if recalled_memories and len(recalled_memories) > self.MAX_RECALL_CHARS:
            recalled_memories = recalled_memories[:self.MAX_RECALL_CHARS] + "\n... [召回过长已截断]"

        if self._should_inject_browser_guide(user_message, recent_messages):
            browser_guide = _BROWSER_GUIDE_FULL
        else:
            browser_guide = _BROWSER_GUIDE_SHORT

        recent_learnings = self._load_recent_learnings(user_message)
        tool_insights = self._build_tool_insights()
        if tool_insights:
            recent_learnings = f"{recent_learnings}\n{tool_insights}" if recent_learnings else tool_insights

        user_profile_section = self._build_user_profile_section(current_user)
        if user_profile_section:
            user_profile_section = f"## 用户画像\n{user_profile_section}"

        milestone_greeting = self._build_milestone_greeting(current_user)
        knowledge_context = self._build_knowledge_context(user_message)

        return SYSTEM_PROMPT_TEMPLATE.format(
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S %A"),
            os_info=f"{platform.system()} {platform.release()} ({platform.machine()})",
            skill_count=len(self.skill_registry.list_all()),
            skills_summary=skills_summary,
            long_term_memory=long_term_memory or "(尚无长期记忆)",
            daily_notes=daily_notes or "(无最近动态)",
            recalled_memories=recalled_memories or "(无相关记忆)",
            current_user=current_user if current_user != "default" else "未识别",
            browser_guide=browser_guide,
            recent_learnings=recent_learnings,
            user_profile_section=user_profile_section,
            milestone_greeting=milestone_greeting,
            knowledge_context=knowledge_context,
        )
