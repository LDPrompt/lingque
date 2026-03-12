"""
🐦 灵雀 - 飞书群聊技能

提供:
- 查找群成员
- @ 群成员发消息
- 列出群成员
"""

import logging
from .registry import register, SkillResult

logger = logging.getLogger("lingque.skills.feishu_group")

_feishu_channel = None


def set_feishu_channel(channel):
    global _feishu_channel
    _feishu_channel = channel


@register(
    name="find_group_member",
    description="在当前飞书群聊中按名字查找成员，返回成员的 open_id 和名字。用于确认群里是否有某人。",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "要查找的群成员名字（支持模糊匹配）"},
        },
        "required": ["name"],
    },
    risk_level="low",
)
async def find_group_member(name: str) -> SkillResult:
    if not _feishu_channel:
        return SkillResult(success=False, error="飞书通道未初始化")

    chat_id = _feishu_channel.get_current_chat_id()
    if not chat_id:
        return SkillResult(success=False, error="当前没有活跃的群聊会话")

    member = await _feishu_channel.find_member_by_name(chat_id, name)
    if member:
        return SkillResult(success=True, data=f"找到群成员: {member['name']} (ID: {member['open_id']})")

    members = await _feishu_channel.get_group_members(chat_id)
    names = [m.get("name", "?") for m in members[:30]]
    return SkillResult(
        success=False,
        error=f"未找到名为 '{name}' 的群成员。当前群成员: {', '.join(names)}",
    )


@register(
    name="send_to_member",
    description=(
        "在飞书群聊中发送消息并 @ 指定的群成员，让对方收到通知。"
        "先用 find_group_member 确认名字，再用此工具发送。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "member_name": {"type": "string", "description": "要 @ 的群成员名字"},
            "content": {"type": "string", "description": "消息内容（支持 Markdown）"},
            "title": {"type": "string", "description": "卡片标题（默认 🐦 灵雀）"},
        },
        "required": ["member_name", "content"],
    },
    risk_level="low",
)
async def send_to_member(member_name: str, content: str, title: str = "🐦 灵雀") -> SkillResult:
    if not _feishu_channel:
        return SkillResult(success=False, error="飞书通道未初始化")

    chat_id = _feishu_channel.get_current_chat_id()
    if not chat_id:
        return SkillResult(success=False, error="当前没有活跃的群聊会话")

    member = await _feishu_channel.find_member_by_name(chat_id, member_name)
    if not member:
        members = await _feishu_channel.get_group_members(chat_id)
        names = [m.get("name", "?") for m in members[:30]]
        return SkillResult(
            success=False,
            error=f"未找到名为 '{member_name}' 的群成员。当前群成员: {', '.join(names)}",
        )

    await _feishu_channel.send_card(
        chat_id, title, content,
        mention_users=[{"open_id": member["open_id"], "name": member["name"]}],
    )
    return SkillResult(success=True, data=f"已发送消息并 @ {member['name']}")


@register(
    name="list_group_members",
    description="列出当前飞书群聊的所有成员名单",
    parameters={
        "type": "object",
        "properties": {},
    },
    risk_level="low",
)
async def list_group_members() -> SkillResult:
    if not _feishu_channel:
        return SkillResult(success=False, error="飞书通道未初始化")

    chat_id = _feishu_channel.get_current_chat_id()
    if not chat_id:
        return SkillResult(success=False, error="当前没有活跃的群聊会话")

    members = await _feishu_channel.get_group_members(chat_id)
    if not members:
        return SkillResult(
            success=False,
            error="未获取到群成员信息（可能需要 im:chat.member:readonly 权限）",
        )

    lines = [f"{i+1}. {m.get('name', '?')}" for i, m in enumerate(members)]
    return SkillResult(
        success=True,
        data=f"当前群聊共 {len(members)} 名成员:\n" + "\n".join(lines),
    )
