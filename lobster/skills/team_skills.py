"""
灵雀 - 多 Agent 团队技能

提供团队状态查看、强制团队模式触发、取消正在执行的团队任务。
"""

import logging
from .registry import register, SkillResult

logger = logging.getLogger("lingque.skills.team")

_agent = None


def set_agent_for_team(agent):
    global _agent
    _agent = agent


@register(
    name="team_status",
    description="查看当前多 Agent 团队的运行状态，包括各角色的执行情况。如果没有正在执行的团队，会显示上一次的团队记录。",
    parameters={"type": "object", "properties": {}},
    risk_level="low",
    category="system",
)
async def team_status() -> SkillResult:
    if not _agent:
        return SkillResult(success=True, data="团队系统未初始化。")
    try:
        ctrl = _agent._get_multi_agent_ctrl()
        return SkillResult(success=True, data=ctrl.get_status())
    except Exception as e:
        return SkillResult(success=False, error=f"获取团队状态失败: {e}")


@register(
    name="team_execute",
    description="启动多 Agent 团队模式来处理复杂任务。灵雀会自动组建团队、分配角色、并行执行、汇总结果。适用于需要同时处理多个独立子目标的任务。",
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "需要团队协作完成的任务描述",
            },
        },
        "required": ["task"],
    },
    risk_level="low",
    category="system",
)
async def team_execute(task: str) -> SkillResult:
    if not _agent:
        return SkillResult(success=False, error="团队系统未初始化。")
    try:
        ctrl = _agent._get_multi_agent_ctrl()
        result = await ctrl.process(task)
        if result:
            return SkillResult(success=True, data=result)
        return SkillResult(
            success=True,
            data="任务不适合拆分为团队协作，建议直接处理。",
        )
    except Exception as e:
        logger.error(f"团队执行失败: {e}")
        return SkillResult(success=False, error=f"团队执行失败: {e}")


@register(
    name="team_cancel",
    description="取消正在执行的多 Agent 团队任务。已完成的子任务结果会保留。",
    parameters={"type": "object", "properties": {}},
    risk_level="low",
    category="system",
)
async def team_cancel() -> SkillResult:
    if not _agent:
        return SkillResult(success=True, data="团队系统未初始化。")
    try:
        ctrl = _agent._get_multi_agent_ctrl()
        if not ctrl._current_team:
            return SkillResult(success=True, data="当前没有团队在执行任务。")
        ctrl.cancel()
        return SkillResult(success=True, data="已发送取消信号，团队任务将在当前步骤完成后停止。")
    except Exception as e:
        return SkillResult(success=False, error=f"取消失败: {e}")
