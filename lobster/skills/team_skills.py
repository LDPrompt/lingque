"""
灵雀 - 多 Agent 团队技能

提供团队状态查看和强制团队模式触发。
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
    description="查看当前多 Agent 团队的运行状态，包括各角色的执行情况",
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
