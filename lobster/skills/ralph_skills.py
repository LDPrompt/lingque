"""
🐦 灵雀 - Ralph Loop 自主循环技能

提供 Ralph Loop 的用户交互接口：
- 创建自主任务
- 暂停/恢复/取消任务
- 查看任务状态
"""

import logging
from .registry import register, SkillResult

logger = logging.getLogger("lingque.skills.ralph")

_feishu_channel = None


def set_feishu_channel_for_ralph(channel):
    """注入飞书通道（获取当前 chat_id）"""
    global _feishu_channel
    _feishu_channel = channel


def _get_ralph_loop():
    """延迟导入，避免循环依赖"""
    from ..agent.ralph_loop import get_ralph_loop
    return get_ralph_loop()


@register(
    name="start_autonomous_task",
    description=(
        "启动一个自主循环任务（Ralph Loop v2.0）。适用于需要长时间持续运行的任务，如：\n"
        "- 监控某个网站的价格/库存变化\n"
        "- 持续跟踪某个 GitHub 仓库的更新\n"
        "- 定期检查某个 API 的数据\n"
        "- 需要多次迭代完成的研究任务\n\n"
        "v2.0 新特性：\n"
        "- LLM 智能判断任务完成（不再依赖关键词）\n"
        "- 任务优先级系统（high/medium/low）\n"
        "- 智能进度摘要和完成度评估"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "任务名称，如 '监控闲鱼XX商品价格'"
            },
            "goal": {
                "type": "string",
                "description": "详细的任务目标描述，告诉 AI 要做什么、怎么做"
            },
            "success_criteria": {
                "type": "string",
                "description": "成功标准：明确定义什么情况下任务算完成（可选，帮助 LLM 更准确判断）"
            },
            "priority": {
                "type": "string",
                "description": "任务优先级: high(间隔减半)/medium(正常)/low(间隔翻倍)",
                "enum": ["high", "medium", "low"],
                "default": "medium"
            },
            "check_interval_minutes": {
                "type": "integer",
                "description": "检查间隔（分钟），默认 30 分钟",
                "default": 30
            },
            "max_iterations": {
                "type": "integer",
                "description": "最大迭代次数，防止无限运行，默认 100 次",
                "default": 100
            },
        },
        "required": ["name", "goal"],
    },
    risk_level="medium",
)
async def start_autonomous_task(
    name: str,
    goal: str,
    success_criteria: str = "",
    priority: str = "medium",
    check_interval_minutes: int = 30,
    max_iterations: int = 100,
) -> SkillResult:
    """创建并启动一个自主循环任务"""
    ralph = _get_ralph_loop()
    if not ralph:
        return SkillResult(
            success=False,
            error="Ralph Loop 未初始化，请检查配置"
        )
    
    # 获取当前会话 ID
    chat_id = ""
    creator_id = ""
    if _feishu_channel:
        chat_id = _feishu_channel.get_current_chat_id()
    
    if not chat_id:
        return SkillResult(
            success=False,
            error="无法获取当前会话 ID，请在飞书中使用此功能"
        )
    
    # 参数校验
    if check_interval_minutes < 5:
        return SkillResult(
            success=False,
            error="检查间隔不能少于 5 分钟"
        )
    if max_iterations < 1 or max_iterations > 1000:
        return SkillResult(
            success=False,
            error="最大迭代次数应在 1-1000 之间"
        )
    
    # 创建任务
    task = ralph.create_task(
        name=name,
        goal=goal,
        chat_id=chat_id,
        creator_id=creator_id,
        check_interval_minutes=check_interval_minutes,
        max_iterations=max_iterations,
        priority=priority,
        success_criteria=success_criteria,
    )
    
    # 启动任务
    ralph.start_task(task.id)
    
    # 计算实际间隔
    effective_interval = task.get_effective_interval()
    priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "🟡")
    
    return SkillResult(
        success=True,
        data=(
            f"✅ 自主任务已启动！\n\n"
            f"📋 **{name}**\n"
            f"🆔 任务 ID: `{task.id}`\n"
            f"🎯 目标: {goal[:100]}{'...' if len(goal) > 100 else ''}\n"
            f"{priority_emoji} 优先级: {priority}\n"
            f"⏱️ 实际检查间隔: {effective_interval:.0f} 分钟\n"
            f"🔄 最大迭代: {max_iterations} 次\n"
            + (f"✓ 成功标准: {success_criteria[:50]}...\n" if success_criteria else "") +
            f"\n任务将在后台持续运行，LLM 智能评估完成度。\n"
            f"你可以随时使用以下命令管理任务：\n"
            f"- 暂停: `pause_autonomous_task('{task.id}')`\n"
            f"- 恢复: `resume_autonomous_task('{task.id}')`\n"
            f"- 取消: `cancel_autonomous_task('{task.id}')`\n"
            f"- 调整优先级: `update_task_priority('{task.id}', 'high')`"
        )
    )


@register(
    name="list_autonomous_tasks",
    description="列出所有自主循环任务及其状态",
    parameters={
        "type": "object",
        "properties": {
            "status_filter": {
                "type": "string",
                "description": "状态筛选: running/paused/completed/cancelled/all",
                "enum": ["running", "paused", "completed", "cancelled", "all"],
                "default": "all"
            },
        },
        "required": [],
    },
    risk_level="low",
)
async def list_autonomous_tasks(status_filter: str = "all") -> SkillResult:
    """列出自主循环任务"""
    ralph = _get_ralph_loop()
    if not ralph:
        return SkillResult(success=False, error="Ralph Loop 未初始化")
    
    from ..agent.ralph_loop import TaskStatus
    
    status = None
    if status_filter != "all":
        try:
            status = TaskStatus(status_filter)
        except ValueError:
            pass
    
    tasks = ralph.list_tasks(status)
    
    if not tasks:
        return SkillResult(
            success=True,
            data="📋 没有自主循环任务" + (f" (筛选: {status_filter})" if status_filter != "all" else "")
        )
    
    status_emoji = {
        "running": "🟢 运行中",
        "paused": "⏸️ 已暂停",
        "pending": "⏳ 待启动",
        "completed": "✅ 已完成",
        "failed": "❌ 失败",
        "cancelled": "🚫 已取消",
    }
    
    lines = [f"📋 **自主循环任务** ({len(tasks)} 个)\n"]
    
    for task in tasks:
        status_text = status_emoji.get(task.status.value, task.status.value)
        lines.append(f"**{task.name}** - {status_text}")
        lines.append(f"  ID: `{task.id}`")
        lines.append(f"  迭代: {task.current_iteration}/{task.max_iterations}")
        if task.last_check_at:
            lines.append(f"  上次检查: {task.last_check_at[:16]}")
        lines.append("")
    
    return SkillResult(success=True, data="\n".join(lines))


@register(
    name="get_autonomous_task_detail",
    description="查看某个自主任务的详细信息和历史记录",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID"},
        },
        "required": ["task_id"],
    },
    risk_level="low",
)
async def get_autonomous_task_detail(task_id: str) -> SkillResult:
    """查看任务详情"""
    ralph = _get_ralph_loop()
    if not ralph:
        return SkillResult(success=False, error="Ralph Loop 未初始化")
    
    task = ralph.get_task(task_id)
    if not task:
        return SkillResult(success=False, error=f"任务不存在: {task_id}")
    
    status_emoji = {
        "running": "🟢", "paused": "⏸️", "pending": "⏳",
        "completed": "✅", "failed": "❌", "cancelled": "🚫",
    }
    
    lines = [
        f"📋 **{task.name}**",
        f"",
        f"🆔 ID: `{task.id}`",
        f"📊 状态: {status_emoji.get(task.status.value, '')} {task.status.value}",
        f"🎯 目标: {task.goal}",
        f"",
        f"⏱️ 检查间隔: {task.check_interval_minutes} 分钟",
        f"🔄 迭代: {task.current_iteration}/{task.max_iterations}",
        f"",
        f"📅 创建时间: {task.created_at[:16] if task.created_at else '-'}",
        f"📅 启动时间: {task.started_at[:16] if task.started_at else '-'}",
        f"📅 上次检查: {task.last_check_at[:16] if task.last_check_at else '-'}",
    ]
    
    if task.context_summary:
        lines.append(f"\n📝 **进度摘要**:\n{task.context_summary[:300]}")
    
    if task.last_result:
        lines.append(f"\n📄 **最近结果**:\n{task.last_result[:300]}...")
    
    if task.history:
        lines.append(f"\n📜 **最近历史** ({len(task.history)} 条):")
        for h in task.history[-5:]:
            lines.append(f"  - [{h.get('action', '?')}] {h.get('result', '')[:50]}")
    
    return SkillResult(success=True, data="\n".join(lines))


@register(
    name="pause_autonomous_task",
    description="暂停一个正在运行的自主任务",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID"},
        },
        "required": ["task_id"],
    },
    risk_level="low",
)
async def pause_autonomous_task(task_id: str) -> SkillResult:
    """暂停任务"""
    ralph = _get_ralph_loop()
    if not ralph:
        return SkillResult(success=False, error="Ralph Loop 未初始化")
    
    task = ralph.get_task(task_id)
    if not task:
        return SkillResult(success=False, error=f"任务不存在: {task_id}")
    
    if ralph.pause_task(task_id):
        return SkillResult(
            success=True,
            data=f"⏸️ 任务 **{task.name}** 已暂停\n\n使用 `resume_autonomous_task('{task_id}')` 恢复"
        )
    else:
        return SkillResult(
            success=False,
            error=f"无法暂停任务（当前状态: {task.status.value}）"
        )


@register(
    name="resume_autonomous_task",
    description="恢复一个已暂停的自主任务",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID"},
        },
        "required": ["task_id"],
    },
    risk_level="low",
)
async def resume_autonomous_task(task_id: str) -> SkillResult:
    """恢复任务"""
    ralph = _get_ralph_loop()
    if not ralph:
        return SkillResult(success=False, error="Ralph Loop 未初始化")
    
    task = ralph.get_task(task_id)
    if not task:
        return SkillResult(success=False, error=f"任务不存在: {task_id}")
    
    if ralph.resume_task(task_id):
        return SkillResult(
            success=True,
            data=f"▶️ 任务 **{task.name}** 已恢复运行"
        )
    else:
        return SkillResult(
            success=False,
            error=f"无法恢复任务（当前状态: {task.status.value}）"
        )


@register(
    name="cancel_autonomous_task",
    description="取消一个自主任务（不可恢复）",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID"},
        },
        "required": ["task_id"],
    },
    risk_level="medium",
)
async def cancel_autonomous_task(task_id: str) -> SkillResult:
    """取消任务"""
    ralph = _get_ralph_loop()
    if not ralph:
        return SkillResult(success=False, error="Ralph Loop 未初始化")
    
    task = ralph.get_task(task_id)
    if not task:
        return SkillResult(success=False, error=f"任务不存在: {task_id}")
    
    name = task.name
    if ralph.cancel_task(task_id):
        return SkillResult(
            success=True,
            data=f"🚫 任务 **{name}** 已取消"
        )
    else:
        return SkillResult(
            success=False,
            error=f"无法取消任务（当前状态: {task.status.value}）"
        )


@register(
    name="delete_autonomous_task",
    description="删除一个已完成或已取消的自主任务记录",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID"},
        },
        "required": ["task_id"],
    },
    risk_level="medium",
)
async def delete_autonomous_task(task_id: str) -> SkillResult:
    """删除任务"""
    ralph = _get_ralph_loop()
    if not ralph:
        return SkillResult(success=False, error="Ralph Loop 未初始化")
    
    task = ralph.get_task(task_id)
    if not task:
        return SkillResult(success=False, error=f"任务不存在: {task_id}")
    
    name = task.name
    if ralph.delete_task(task_id):
        return SkillResult(
            success=True,
            data=f"🗑️ 任务 **{name}** 已删除"
        )
    else:
        return SkillResult(success=False, error="删除失败")


@register(
    name="update_task_priority",
    description="更新自主任务的优先级。高优先级任务检查间隔减半，低优先级任务检查间隔翻倍。",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID"},
            "priority": {
                "type": "string",
                "description": "新的优先级",
                "enum": ["high", "medium", "low"]
            },
        },
        "required": ["task_id", "priority"],
    },
    risk_level="low",
)
async def update_task_priority(task_id: str, priority: str) -> SkillResult:
    """更新任务优先级"""
    ralph = _get_ralph_loop()
    if not ralph:
        return SkillResult(success=False, error="Ralph Loop 未初始化")
    
    task = ralph.get_task(task_id)
    if not task:
        return SkillResult(success=False, error=f"任务不存在: {task_id}")
    
    old_priority = task.priority.value
    if ralph.update_priority(task_id, priority):
        task = ralph.get_task(task_id)
        effective_interval = task.get_effective_interval()
        priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "🟡")
        
        return SkillResult(
            success=True,
            data=(
                f"{priority_emoji} 任务 **{task.name}** 优先级已更新\n\n"
                f"  {old_priority} → {priority}\n"
                f"  实际检查间隔: {effective_interval:.0f} 分钟"
            )
        )
    else:
        return SkillResult(success=False, error=f"更新失败，无效的优先级: {priority}")
