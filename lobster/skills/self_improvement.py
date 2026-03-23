"""
🐦 灵雀 - 自我学习技能

提供结构化的错误/学习/最佳实践记录机制。
记录存储在 {WORKSPACE_DIR}/.learnings/LEARNINGS.jsonl，
每行一个 JSON 对象，context.py 会读取最近记录注入 system prompt。
"""

import collections
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from .registry import register, SkillResult

logger = logging.getLogger("lingque.skills.self_improvement")

_workspace_dir: str | None = None


def set_workspace_dir(workspace_dir: str):
    global _workspace_dir
    _workspace_dir = workspace_dir


def _get_learnings_path() -> Path:
    base = _workspace_dir or os.environ.get("WORKSPACE_DIR", "./workspaces")
    p = Path(base) / ".learnings" / "LEARNINGS.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _write_entry(entry: dict):
    """统一的记录写入：优先走 learning_engine，降级到直接追加文件"""
    try:
        from ..memory.learning_engine import get_learning_engine
        le = get_learning_engine()
        if le:
            le.record(entry)
            return
    except Exception:
        pass
    path = _get_learnings_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def auto_log_error(tool_name: str, error_message: str, context: str = ""):
    """非 LLM 调用的自动记录 — 直接追加文件，零开销"""
    try:
        _write_entry({
            "type": "auto_error",
            "timestamp": datetime.now().isoformat(),
            "category": "tool_error",
            "context": f"工具 {tool_name}: {context}"[:200],
            "error_message": str(error_message)[:500],
            "fix": "",
        })
    except Exception as e:
        logger.debug(f"自动记录错误日志失败: {e}")


@register(
    name="log_learning",
    description="记录学习/纠正/最佳实践，帮助自我改进避免重复犯错",
    parameters={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "分类: browser/file/code/api/user_pref/general",
            },
            "context": {
                "type": "string",
                "description": "学习的场景/上下文（简短描述）",
            },
            "learning": {
                "type": "string",
                "description": "具体学到了什么，或最佳实践",
            },
        },
        "required": ["category", "context", "learning"],
    },
    risk_level="low",
    category="self_improvement",
)
async def log_learning(category: str, context: str, learning: str) -> SkillResult:
    try:
        _write_entry({
            "type": "learning",
            "timestamp": datetime.now().isoformat(),
            "category": category,
            "context": context[:200],
            "learning": learning[:500],
        })
        return SkillResult(success=True, data=f"已记录学习: [{category}] {learning[:80]}")
    except Exception as e:
        return SkillResult(success=False, error=str(e))


@register(
    name="log_error",
    description="记录遇到的错误和修复方法，方便以后避免同样的问题",
    parameters={
        "type": "object",
        "properties": {
            "context": {
                "type": "string",
                "description": "出错的场景/操作",
            },
            "error_message": {
                "type": "string",
                "description": "错误信息",
            },
            "fix": {
                "type": "string",
                "description": "修复方法或应对策略",
            },
        },
        "required": ["context", "error_message"],
    },
    risk_level="low",
    category="self_improvement",
)
async def log_error(context: str, error_message: str, fix: str = "") -> SkillResult:
    try:
        _write_entry({
            "type": "error",
            "timestamp": datetime.now().isoformat(),
            "category": "manual_error",
            "context": context[:200],
            "error_message": error_message[:500],
            "fix": fix[:300],
        })
        return SkillResult(success=True, data=f"已记录错误: {error_message[:80]}")
    except Exception as e:
        return SkillResult(success=False, error=str(e))


@register(
    name="review_learnings",
    description="回顾最近的学习记录，帮助做出更好的决策",
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "返回记录数（默认 5）",
            },
            "category": {
                "type": "string",
                "description": "按分类筛选（可选）",
            },
        },
    },
    risk_level="low",
    category="self_improvement",
)
async def review_learnings(limit: int = 5, category: str = "") -> SkillResult:
    try:
        path = _get_learnings_path()
        if not path.exists():
            return SkillResult(success=True, data="暂无学习记录。")

        max_tail = max(limit * 3, 100)
        with open(path, "r", encoding="utf-8") as f:
            lines = collections.deque(f, maxlen=max_tail)

        entries = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if category and entry.get("category") != category:
                    continue
                entries.append(entry)
                if len(entries) >= limit:
                    break
            except json.JSONDecodeError:
                continue

        if not entries:
            return SkillResult(success=True, data="暂无匹配的学习记录。")

        output_lines = []
        for e in entries:
            ts = e.get("timestamp", "")[:16]
            cat = e.get("category", "")
            if e.get("type") == "error" or e.get("type") == "auto_error":
                msg = e.get("error_message", "")
                fix = e.get("fix", "")
                line_str = f"[{ts}] ❌ [{cat}] {e.get('context', '')}: {msg}"
                if fix:
                    line_str += f" → 修复: {fix}"
            else:
                line_str = f"[{ts}] 📝 [{cat}] {e.get('context', '')}: {e.get('learning', '')}"
            output_lines.append(line_str)

        return SkillResult(success=True, data="\n".join(output_lines))
    except Exception as e:
        return SkillResult(success=False, error=str(e))


# =====================================================================
# 版本升级技能
# =====================================================================

@register(
    name="check_update",
    description="检查灵雀是否有新版本可用，显示更新日志",
    parameters={"type": "object", "properties": {}},
    risk_level="low",
    category="system",
)
async def check_update() -> SkillResult:
    try:
        from ..updater import check_for_updates, get_current_version
        result = await check_for_updates()
        if result["has_update"]:
            notes = result["release_notes"]
            if len(notes) > 500:
                notes = notes[:500] + "..."
            msg = (
                f"🆕 **发现新版本 v{result['latest']}**\n"
                f"当前版本: v{result['current']}\n"
            )
            if result["published_at"]:
                msg += f"发布时间: {result['published_at'][:10]}\n"
            if notes:
                msg += f"\n📋 更新内容:\n{notes}\n"
            if result["release_url"]:
                msg += f"\n🔗 {result['release_url']}\n"
            msg += "\n💡 说「升级」即可一键更新"
            return SkillResult(success=True, data=msg)
        else:
            return SkillResult(
                success=True,
                data=f"✅ 当前已是最新版本 v{result['current']}，无需升级",
            )
    except Exception as e:
        return SkillResult(success=False, error=f"版本检查失败: {e}")


@register(
    name="perform_upgrade",
    description="执行灵雀系统升级。会自动拉取最新代码、安装依赖、迁移数据并重启服务。升级不会影响记忆和用户数据。",
    parameters={"type": "object", "properties": {}},
    risk_level="high",
    category="system",
)
async def perform_upgrade() -> SkillResult:
    try:
        from ..updater import check_for_updates, perform_upgrade as do_upgrade

        check = await check_for_updates()
        if not check["has_update"]:
            return SkillResult(
                success=True,
                data=f"当前已是最新版本 v{check['current']}，无需升级",
            )

        progress_msgs = []

        async def _on_progress(msg: str):
            progress_msgs.append(msg)

        result = await do_upgrade(notify_callback=_on_progress)

        if result["success"]:
            return SkillResult(
                success=True,
                data=(
                    f"🎉 升级成功! v{result['from_version']} → v{result['to_version']}\n\n"
                    f"升级日志:\n" + "\n".join(result["steps"]) +
                    "\n\n⚠️ 服务即将重启，请稍候..."
                ),
            )
        else:
            return SkillResult(
                success=False,
                error=(
                    f"升级失败: {result['error']}\n\n"
                    f"已执行步骤:\n" + "\n".join(result["steps"])
                ),
            )
    except Exception as e:
        return SkillResult(success=False, error=f"升级执行异常: {e}")
