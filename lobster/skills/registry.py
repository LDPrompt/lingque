"""
🐦 技能注册中心
所有 Skill 通过装饰器注册，Agent 通过 Registry 发现和调用技能
"""

import logging
import inspect
import os
from typing import Any, Callable, Awaitable, Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("lobster.skills")

_security_logger: logging.Logger | None = None


def _get_security_logger() -> logging.Logger:
    global _security_logger
    if _security_logger is None:
        _security_logger = logging.getLogger("lobster.security_audit")
        _security_logger.setLevel(logging.INFO)
        if not _security_logger.handlers:
            log_dir = os.environ.get("MEMORY_DIR", "logs")
            os.makedirs(log_dir, exist_ok=True)
            fh = logging.FileHandler(
                os.path.join(log_dir, "security_audit.log"), encoding="utf-8"
            )
            fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
            _security_logger.addHandler(fh)
            _security_logger.propagate = False
    return _security_logger


@dataclass
class SkillResult:
    """技能执行结果"""
    success: bool
    data: str = ""
    error: str = ""

    def __str__(self) -> str:
        if self.success:
            return self.data or "操作成功"
        return f"错误: {self.error}"


@dataclass
class SkillDefinition:
    """技能定义"""
    name: str                       # 工具名, 如 "read_file"
    description: str                # 描述，给 LLM 看的
    parameters: dict                # JSON Schema 参数定义
    handler: Callable[..., Awaitable[str]]  # 实际执行函数
    risk_level: str = "low"         # low / medium / high
    category: str = "general"       # 分类


class SkillRegistry:
    """技能注册中心 - 管理所有可用工具"""

    def __init__(self):
        self._skills: dict[str, SkillDefinition] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        risk_level: str = "low",
        category: str = "general",
    ):
        """装饰器：注册一个技能

        用法:
            @registry.register(
                name="read_file",
                description="读取文件内容",
                parameters={...},
            )
            async def read_file(path: str) -> str:
                ...
        """
        def decorator(func: Callable[..., Awaitable[str]]):
            skill = SkillDefinition(
                name=name,
                description=description,
                parameters=parameters,
                handler=func,
                risk_level=risk_level,
                category=category,
            )
            self._skills[name] = skill
            logger.info(f"注册技能: {name} [{category}] (风险: {risk_level})")
            return func
        return decorator

    def get(self, name: str) -> SkillDefinition | None:
        return self._skills.get(name)

    def list_all(self) -> list[SkillDefinition]:
        return list(self._skills.values())

    def to_tool_definitions(self) -> list[dict]:
        """导出为 LLM tool calling 格式（全量）"""
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "parameters": skill.parameters,
            }
            for skill in self._skills.values()
        ]

    # ---- 动态工具过滤 ----

    _ALWAYS_CATEGORIES = {"general", "file", "code", "memory", "system", "self_improvement", "credential"}

    _KEYWORD_TO_CATEGORY: dict[str, set[str]] = {
        "browser": {"browser", "web", "search"},
        "web":     {"browser", "web", "search"},
        "网页":    {"browser", "web", "search"},
        "浏览器":  {"browser", "web", "search"},
        "搜索":    {"browser", "web", "search"},
        "打开":    {"browser", "web", "search"},
        "登录":    {"browser", "web", "search"},
        "抖音":    {"browser", "web", "search"},
        "邮件":    {"email", "calendar"},
        "email":   {"email", "calendar"},
        "日程":    {"email", "calendar"},
        "日历":    {"email", "calendar"},
        "提醒":    {"email", "calendar", "scheduler", "reminder"},
        "闹钟":    {"reminder", "scheduler"},
        "定时":    {"scheduler"},
        "cron":    {"scheduler"},
        "调度":    {"scheduler"},
        "知识":    {"knowledge"},
        "实体":    {"knowledge"},
        "关系":    {"knowledge"},
        "图谱":    {"knowledge"},
        "工作流":  {"workflow"},
        "workflow": {"workflow"},
        "自动":    {"workflow", "ralph"},
        "后台":    {"ralph", "scheduler"},
        "mcp":     {"mcp"},
        "服务":    {"mcp"},
        "飞书":    {"feishu", "feishu_group"},
        "群":      {"feishu_group"},
        "文档":    {"feishu", "file"},
        "云文档":  {"feishu"},
        "技能市场": {"skill_market", "transplanted"},
        "安装技能": {"skill_market", "transplanted"},
        "已安装":  {"transplanted", "plugin"},
        "生成技能": {"skill_generator"},
        "插件":    {"plugin", "transplanted"},
        "团队":    {"system"},
        "组队":    {"system"},
        "助手":    {"system"},
        "密钥":    {"credential"},
        "凭证":    {"credential"},
        "api_key": {"credential"},
        "token":   {"credential"},
        "secret":  {"credential"},
    }

    _FALLBACK_CATEGORIES = {"browser", "web", "search", "scheduler", "knowledge", "workflow", "transplanted", "plugin", "mcp", "feishu"}

    def get_tools_by_categories(self, categories: list[str]) -> list[dict]:
        """根据技能类别列表返回对应的工具定义（供子 Agent 使用）"""
        cat_set = set(categories) | set(self._ALWAYS_CATEGORIES)
        selected = []
        for skill in self._skills.values():
            if skill.category in cat_set:
                selected.append({
                    "name": skill.name,
                    "description": skill.description,
                    "parameters": skill.parameters,
                })
        return selected

    def get_skills_by_categories(self, categories: list[str]) -> list["SkillDefinition"]:
        """根据技能类别列表返回 SkillDefinition 列表（含 category 信息）"""
        cat_set = set(categories) | set(self._ALWAYS_CATEGORIES)
        return [s for s in self._skills.values() if s.category in cat_set]

    def select_tools_for_task(self, user_message: str,
                              recent_tool_names: list[str] | None = None) -> list[dict]:
        """根据用户消息智能选择相关工具子集，大幅减少 token 开销"""
        needed_cats: set[str] = set(self._ALWAYS_CATEGORIES)

        matched_keyword_cats: set[str] = set()
        msg_lower = user_message.lower()
        for keyword, cats in self._KEYWORD_TO_CATEGORY.items():
            if keyword in msg_lower:
                matched_keyword_cats.update(cats)

        if recent_tool_names:
            for name in recent_tool_names:
                skill = self._skills.get(name)
                if skill:
                    needed_cats.add(skill.category)

        if len(matched_keyword_cats) <= 2:
            needed_cats.update(self._FALLBACK_CATEGORIES)
        needed_cats.update(matched_keyword_cats)

        selected = []
        for skill in self._skills.values():
            if skill.category in needed_cats:
                selected.append({
                    "name": skill.name,
                    "description": skill.description,
                    "parameters": skill.parameters,
                })

        logger.info(f"工具过滤: {len(self._skills)} → {len(selected)} "
                    f"(类别: {sorted(needed_cats)})")
        return selected

    max_result_chars: int = 0

    async def execute_raw(self, name: str, arguments: dict[str, Any]) -> SkillResult:
        """执行技能，返回结构化 SkillResult（推荐工作流引擎使用）"""
        skill = self._skills.get(name)
        if not skill:
            return SkillResult(success=False, error=f"未知技能 '{name}'")

        try:
            import inspect
            sig = inspect.signature(skill.handler)
            valid_params = set(sig.parameters.keys())
            has_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            if not has_kwargs:
                filtered = {k: v for k, v in arguments.items() if k in valid_params}
                if len(filtered) < len(arguments):
                    dropped = set(arguments) - set(filtered)
                    logger.warning(f"技能 {name}: 过滤非法参数 {dropped}")
                arguments = filtered

            _log_args = arguments
            if name in ("save_credential", "get_credential", "delete_credential"):
                _log_args = {k: ("***" if k == "value" else v) for k, v in arguments.items()}
            logger.info(f"执行技能: {name}({_log_args})")

            if skill.risk_level in ("medium", "high"):
                try:
                    _get_security_logger().info(
                        f"EXEC | risk={skill.risk_level} | skill={name} | args={_log_args}"
                    )
                except Exception:
                    pass

            result = await skill.handler(**arguments)
            if not isinstance(result, SkillResult):
                result = SkillResult(success=True, data=str(result))
            logger.info(f"技能 {name} 执行完成")
            return result
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error(f"技能 {name} 执行失败: {error_msg}")
            return SkillResult(success=False, error=error_msg)

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """执行技能，返回字符串（兼容旧调用方）"""
        result = await self.execute_raw(name, arguments)
        result_str = str(result)
        if self.max_result_chars > 0 and len(result_str) > self.max_result_chars:
            truncated = result_str[:self.max_result_chars]
            logger.warning(
                f"技能 {name} 结果过长 ({len(result_str)} 字符)，已截断到 {self.max_result_chars}"
            )
            result_str = truncated + f"\n\n... [结果已截断，原始长度 {len(result_str)} 字符]"
        return result_str


# 全局技能注册中心
registry = SkillRegistry()

# 便捷别名，允许 from .registry import register
register = registry.register
