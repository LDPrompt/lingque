"""
🐦 灵雀 - 技能市场聊天技能

让用户在飞书里操作 ClawHub 技能市场
"""

import logging
from typing import Optional

from .registry import registry, SkillResult

logger = logging.getLogger("lingque.skills.market")

_transplanter = None


def set_transplanter(transplanter):
    """注入移植器实例"""
    global _transplanter
    _transplanter = transplanter
    logger.info("技能移植器已注入")


@registry.register(
    name="browse_skill_market",
    description="浏览 ClawHub 技能市场，搜索并安装新技能。不带参数显示状态，带关键词则搜索安装",
    parameters={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词（英文），如 'weather', 'translate', 'json'。为空则显示市场状态",
            },
        },
        "required": [],
    },
    risk_level="medium",
    category="system",
)
async def browse_skill_market(keyword: str = "") -> SkillResult:
    """浏览技能市场"""
    if _transplanter is None:
        return SkillResult(
            success=False,
            error="技能移植器未初始化"
        )

    try:
        result = await _transplanter.search_and_install(keyword)
        return SkillResult(success=True, data=result)
    except Exception as e:
        logger.error(f"浏览技能市场失败: {e}")
        return SkillResult(success=False, error=str(e))


@registry.register(
    name="install_skill_from_repo",
    description="从任意 GitHub 仓库安装技能。仓库中需要有 SKILL.md 文件来描述技能",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": "GitHub 仓库地址，如 'https://github.com/owner/repo' 或 'owner/repo'",
            },
        },
        "required": ["repo_url"],
    },
    risk_level="medium",
    category="system",
)
async def install_skill_from_repo(repo_url: str) -> SkillResult:
    """从 GitHub 仓库安装技能"""
    if _transplanter is None:
        return SkillResult(
            success=False,
            error="技能移植器未初始化"
        )

    try:
        result = await _transplanter.install_from_github_repo(repo_url)
        return SkillResult(success=True, data=result)
    except Exception as e:
        logger.error(f"从仓库安装技能失败: {e}")
        return SkillResult(success=False, error=str(e))


@registry.register(
    name="scan_skill_market",
    description="手动触发一次技能市场全量巡检，自动发现并安装新技能（每次最多5个）",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk_level="medium",
    category="system",
)
async def scan_skill_market() -> SkillResult:
    """扫描技能市场"""
    if _transplanter is None:
        return SkillResult(
            success=False,
            error="技能移植器未初始化"
        )

    try:
        result = await _transplanter.run_daily_scan()
        return SkillResult(success=True, data=result)
    except Exception as e:
        logger.error(f"扫描技能市场失败: {e}")
        return SkillResult(success=False, error=str(e))


@registry.register(
    name="list_transplanted_skills",
    description="列出所有已从 ClawHub 移植安装的技能",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk_level="low",
    category="system",
)
async def list_transplanted_skills() -> SkillResult:
    """列出移植的技能"""
    if _transplanter is None:
        return SkillResult(
            success=False,
            error="技能移植器未初始化"
        )

    return SkillResult(success=True, data=_transplanter.list_installed())
