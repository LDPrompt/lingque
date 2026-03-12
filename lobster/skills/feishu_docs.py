"""
🐦 灵雀 - 飞书云文档技能

提供:
- 创建飞书在线文档
- 写入内容（Markdown 自动转换为飞书 Block）
- 分享文档给当前聊天（授权 + 发链接）
"""

import logging
import re

import httpx

from .registry import register, SkillResult

logger = logging.getLogger("lingque.skills.feishu_docs")

FEISHU_API = "https://open.feishu.cn/open-apis"

_feishu_channel = None


def set_feishu_channel(channel):
    global _feishu_channel
    _feishu_channel = channel


# ==================== 飞书 API 请求封装 ====================

async def _feishu_request(method: str, path: str, json_body: dict | None = None,
                          params: dict | None = None) -> dict:
    channel = _feishu_channel
    if not channel:
        return {"code": -1, "msg": "飞书通道未初始化"}
    await channel._ensure_token()
    headers = {
        "Authorization": f"Bearer {channel._tenant_access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    url = f"{FEISHU_API}{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(method, url, json=json_body,
                                    params=params, headers=headers)
        return resp.json()


# ==================== Markdown -> Block 转换 ====================

_BLOCK_TEXT = 2
_BLOCK_H1 = 3
_BLOCK_H2 = 4
_BLOCK_H3 = 5
_BLOCK_H4 = 6
_BLOCK_BULLET = 12
_BLOCK_ORDERED = 13
_BLOCK_CODE = 14
_BLOCK_QUOTE = 15
_BLOCK_DIVIDER = 22

_HEADING_KEY = {3: "heading1", 4: "heading2", 5: "heading3", 6: "heading4"}


def _parse_inline(text: str) -> list[dict]:
    """解析行内样式（粗体、斜体、行内代码、链接），返回 text_run 元素列表"""
    elements = []
    pattern = re.compile(
        r"(\*\*(.+?)\*\*)"       # **bold**
        r"|(\*(.+?)\*)"          # *italic*
        r"|(`(.+?)`)"            # `code`
        r"|(\[(.+?)\]\((.+?)\))" # [text](url)
    )
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            elements.append({"text_run": {"content": text[pos:m.start()]}})
        if m.group(2):
            elements.append({"text_run": {
                "content": m.group(2),
                "text_element_style": {"bold": True},
            }})
        elif m.group(4):
            elements.append({"text_run": {
                "content": m.group(4),
                "text_element_style": {"italic": True},
            }})
        elif m.group(6):
            elements.append({"text_run": {
                "content": m.group(6),
                "text_element_style": {"inline_code": True},
            }})
        elif m.group(8):
            elements.append({"text_run": {
                "content": m.group(8),
                "text_element_style": {"link": {"url": m.group(9)}},
            }})
        pos = m.end()
    if pos < len(text):
        elements.append({"text_run": {"content": text[pos:]}})
    if not elements:
        elements.append({"text_run": {"content": text}})
    return elements


def _text_block(text: str) -> dict:
    return {"block_type": _BLOCK_TEXT, "text": {"elements": _parse_inline(text)}}


def _heading_block(level: int, text: str) -> dict:
    bt = _BLOCK_H1 + level - 1
    key = _HEADING_KEY.get(bt, "heading1")
    return {"block_type": bt, key: {"elements": _parse_inline(text)}}


def _bullet_block(text: str) -> dict:
    return {"block_type": _BLOCK_BULLET, "bullet": {"elements": _parse_inline(text)}}


def _ordered_block(text: str) -> dict:
    return {"block_type": _BLOCK_ORDERED, "ordered": {"elements": _parse_inline(text)}}


def _quote_block(text: str) -> dict:
    return {"block_type": _BLOCK_QUOTE, "quote": {"elements": _parse_inline(text)}}


def _code_block(code: str, language: int = 1) -> dict:
    return {
        "block_type": _BLOCK_CODE,
        "code": {
            "elements": [{"text_run": {"content": code}}],
            "style": {"language": language, "wrap": False},
        },
    }


def _divider_block() -> dict:
    return {"block_type": _BLOCK_DIVIDER, "divider": {}}


_CODE_LANG_MAP = {
    "python": 40, "py": 40, "javascript": 23, "js": 23, "typescript": 49,
    "ts": 49, "go": 20, "java": 22, "rust": 44, "c": 12, "cpp": 13,
    "css": 17, "html": 21, "shell": 46, "bash": 46, "sql": 47,
    "json": 25, "yaml": 56, "dockerfile": 19, "": 1,
}


def _markdown_to_blocks(md: str) -> list[dict]:
    """将 Markdown 文本转为飞书 Block 列表"""
    blocks: list[dict] = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            lang_hint = stripped[3:].strip().lower()
            lang_code = _CODE_LANG_MAP.get(lang_hint, 1)
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            blocks.append(_code_block("\n".join(code_lines), lang_code))
            continue

        if stripped.startswith("#### "):
            blocks.append(_heading_block(4, stripped[5:]))
        elif stripped.startswith("### "):
            blocks.append(_heading_block(3, stripped[4:]))
        elif stripped.startswith("## "):
            blocks.append(_heading_block(2, stripped[3:]))
        elif stripped.startswith("# "):
            blocks.append(_heading_block(1, stripped[2:]))
        elif stripped.startswith("---") or stripped.startswith("***"):
            blocks.append(_divider_block())
        elif stripped.startswith("- ") or stripped.startswith("* "):
            blocks.append(_bullet_block(stripped[2:]))
        elif re.match(r"^\d+\.\s", stripped):
            blocks.append(_ordered_block(re.sub(r"^\d+\.\s", "", stripped)))
        elif stripped.startswith("> "):
            blocks.append(_quote_block(stripped[2:]))
        else:
            blocks.append(_text_block(stripped))

        i += 1

    return blocks


# ==================== 技能定义 ====================

@register(
    name="create_feishu_doc",
    description=(
        "创建一篇飞书在线文档。返回文档 ID 和 URL。"
        "创建后可用 write_feishu_doc 写入内容，用 share_feishu_doc 分享给用户。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "文档标题",
            },
            "folder_token": {
                "type": "string",
                "description": "目标文件夹 token（可选，留空则创建在应用根目录）",
            },
        },
        "required": ["title"],
    },
    risk_level="medium",
    category="feishu",
)
async def create_feishu_doc(title: str, folder_token: str = "") -> SkillResult:
    body: dict = {"title": title}
    if folder_token:
        body["folder_token"] = folder_token

    data = await _feishu_request("POST", "/docx/v1/documents", json_body=body)

    if data.get("code") != 0:
        msg = data.get("msg", str(data))
        return SkillResult(success=False, error=f"创建文档失败: {msg}")

    doc = data.get("data", {}).get("document", {})
    doc_id = doc.get("document_id", "")
    url = f"https://bytedance.feishu.cn/docx/{doc_id}"

    return SkillResult(
        success=True,
        data=f"文档已创建\n标题: {title}\nID: {doc_id}\nURL: {url}\n\n"
             f"接下来可以用 write_feishu_doc 写入内容，用 share_feishu_doc 分享。",
    )


@register(
    name="write_feishu_doc",
    description=(
        "向飞书文档写入内容。支持 Markdown 格式，自动转换为飞书文档结构。"
        "支持标题、正文、粗体、斜体、代码块、列表、引用、分割线等。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "文档 ID（从 create_feishu_doc 获取）",
            },
            "content": {
                "type": "string",
                "description": "要写入的内容（Markdown 或纯文本）",
            },
            "content_type": {
                "type": "string",
                "description": "内容格式: markdown（默认，自动转换标题/列表/代码块等）或 text（纯文本段落）",
            },
        },
        "required": ["document_id", "content"],
    },
    risk_level="medium",
    category="feishu",
)
async def write_feishu_doc(document_id: str, content: str,
                           content_type: str = "markdown") -> SkillResult:
    if content_type == "text":
        blocks = [_text_block(line) for line in content.split("\n") if line.strip()]
    else:
        blocks = _markdown_to_blocks(content)
    if not blocks:
        return SkillResult(success=False, error="内容为空，无法写入")

    MAX_BATCH = 50
    total_written = 0
    for start in range(0, len(blocks), MAX_BATCH):
        batch = blocks[start:start + MAX_BATCH]
        path = f"/docx/v1/documents/{document_id}/blocks/{document_id}/children"
        body = {"children": batch, "index": -1}
        data = await _feishu_request(
            "POST", path, json_body=body,
            params={"document_revision_id": "-1"},
        )
        if data.get("code") != 0:
            msg = data.get("msg", str(data))
            return SkillResult(
                success=False,
                error=f"写入失败 (已写入 {total_written} 块): {msg}",
            )
        total_written += len(batch)

    return SkillResult(
        success=True,
        data=f"已写入 {total_written} 个内容块到文档 {document_id}",
    )


@register(
    name="share_feishu_doc",
    description=(
        "将飞书文档分享给当前聊天。自动添加文档访问权限，并发送文档链接卡片。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "文档 ID",
            },
            "title": {
                "type": "string",
                "description": "文档标题（用于卡片显示）",
            },
            "perm": {
                "type": "string",
                "description": "权限类型: edit（可编辑，默认）或 view（只读）",
            },
        },
        "required": ["document_id"],
    },
    risk_level="low",
    category="feishu",
)
async def share_feishu_doc(document_id: str, title: str = "飞书文档",
                           perm: str = "edit") -> SkillResult:
    if not _feishu_channel:
        return SkillResult(success=False, error="飞书通道未初始化")

    chat_id = _feishu_channel.get_current_chat_id()
    if not chat_id:
        return SkillResult(success=False, error="当前没有活跃的飞书会话")

    perm_path = f"/drive/v1/permissions/{document_id}/members"
    perm_body = {
        "member_type": "openchat",
        "member_id": chat_id,
        "perm": perm,
    }
    perm_data = await _feishu_request(
        "POST", perm_path, json_body=perm_body,
        params={"type": "docx", "need_notification": "false"},
    )

    perm_ok = perm_data.get("code") == 0
    if not perm_ok:
        perm_msg = perm_data.get("msg", "")
        logger.warning(f"文档权限设置可能失败: {perm_msg}")

    doc_url = f"https://bytedance.feishu.cn/docx/{document_id}"
    perm_label = "可编辑" if perm == "edit" else "只读"

    try:
        await _feishu_channel.send_card(
            chat_id,
            f"📄 {title}",
            f"文档已准备好，点击查看:\n\n"
            f"[👉 打开文档]({doc_url})\n\n"
            f"权限: {perm_label}",
        )
        sent = True
    except Exception as e:
        logger.error(f"发送文档卡片失败: {e}")
        sent = False

    parts = [f"文档链接: {doc_url}"]
    if perm_ok:
        parts.append(f"已授权当前聊天 {perm_label} 权限")
    else:
        parts.append(f"权限设置可能未生效（{perm_data.get('msg', '未知')}），请检查应用是否有 drive:permission:member 权限")
    if sent:
        parts.append("文档卡片已发送到聊天")
    return SkillResult(success=True, data="\n".join(parts))
