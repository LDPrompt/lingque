"""
🐦 技能: 文件操作
读取、写入、列表、搜索本地文件
"""

import os
import glob
from pathlib import Path
from .registry import registry

# ============================================================
# 安全检查: 确保只能操作允许的目录
# ============================================================

_allowed_paths: list[str] = []


def set_allowed_paths(paths: list[str]):
    global _allowed_paths
    _allowed_paths = [os.path.abspath(p) for p in paths]


def _check_path(path: str) -> str:
    """校验路径安全性，返回绝对路径（resolve 消除符号链接和 ..）"""
    abs_path = str(Path(path).resolve())
    if not _allowed_paths:
        raise PermissionError(
            "安全限制: 未配置 ALLOWED_PATHS，拒绝所有文件操作"
        )
    if not any(abs_path == ap or abs_path.startswith(ap + os.sep) for ap in _allowed_paths):
        raise PermissionError(
            f"安全限制: 不允许访问 {abs_path}\n"
            f"允许的目录: {_allowed_paths}"
        )
    return abs_path


def _check_resolved_path(file_path: Path, base_path: Path) -> bool:
    """检查 glob 结果是否在允许的基目录内（防止 ../  穿越）"""
    try:
        file_path.resolve().relative_to(base_path.resolve())
        return True
    except ValueError:
        return False


# ============================================================
# 技能注册
# ============================================================

@registry.register(
    name="read_file",
    description="读取文件内容。支持文本文件。返回文件内容字符串。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "encoding": {"type": "string", "description": "编码，默认 utf-8", "default": "utf-8"},
        },
        "required": ["path"],
    },
    risk_level="low",
    category="file",
)
async def read_file(path: str, encoding: str = "utf-8") -> str:
    abs_path = _check_path(path)
    p = Path(abs_path)
    if not p.exists():
        return f"文件不存在: {path}"
    if not p.is_file():
        return f"不是文件: {path}"
    if p.stat().st_size > 1_000_000:  # 1MB 限制
        return f"文件过大 ({p.stat().st_size} bytes), 请指定范围读取"
    return p.read_text(encoding=encoding)


@registry.register(
    name="write_file",
    description="写入内容到文件。如果文件不存在会自动创建。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "要写入的内容"},
            "mode": {
                "type": "string",
                "description": "写入模式: 'w' 覆盖, 'a' 追加",
                "enum": ["w", "a"],
                "default": "w",
            },
        },
        "required": ["path", "content"],
    },
    risk_level="medium",
    category="file",
)
async def write_file(path: str, content: str, mode: str = "w") -> str:
    MAX_WRITE_SIZE = 50_000  # 50KB
    if len(content) > MAX_WRITE_SIZE:
        return (
            f"写入内容过大 ({len(content)} 字符, 限制 {MAX_WRITE_SIZE})。"
            f"请拆分为多次写入或精简内容。"
        )
    abs_path = _check_path(path)
    p = Path(abs_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, mode, encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {len(content)} 字符到 {path}"


@registry.register(
    name="list_directory",
    description="列出目录中的文件和子目录",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径"},
            "pattern": {"type": "string", "description": "glob 模式过滤, 如 '*.py'", "default": "*"},
            "recursive": {"type": "boolean", "description": "是否递归", "default": False},
        },
        "required": ["path"],
    },
    risk_level="low",
    category="file",
)
async def list_directory(path: str, pattern: str = "*", recursive: bool = False) -> str:
    abs_path = _check_path(path)
    p = Path(abs_path)
    if not p.is_dir():
        return f"不是目录: {path}"

    if recursive:
        items = sorted(p.rglob(pattern))
    else:
        items = sorted(p.glob(pattern))

    items = [item for item in items if _check_resolved_path(item, p)][:200]
    lines = []
    for item in items:
        rel = item.relative_to(p)
        prefix = "📁 " if item.is_dir() else "📄 "
        size = f" ({item.stat().st_size:,}B)" if item.is_file() else ""
        lines.append(f"{prefix}{rel}{size}")

    return f"目录 {path} 下共 {len(lines)} 项:\n" + "\n".join(lines)


@registry.register(
    name="search_files",
    description="在文件中搜索包含指定文本的行",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "搜索的根目录"},
            "query": {"type": "string", "description": "搜索的文本"},
            "file_pattern": {"type": "string", "description": "文件 glob 模式", "default": "**/*"},
            "max_results": {"type": "integer", "description": "最大结果数", "default": 20},
        },
        "required": ["path", "query"],
    },
    risk_level="low",
    category="file",
)
async def search_files(
    path: str, query: str, file_pattern: str = "**/*", max_results: int = 20
) -> str:
    abs_path = _check_path(path)
    p = Path(abs_path)
    results = []

    for file_path in p.glob(file_pattern):
        if not file_path.is_file() or not _check_resolved_path(file_path, p):
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                if query.lower() in line.lower():
                    results.append(f"{file_path.relative_to(p)}:{i}: {line.strip()}")
                    if len(results) >= max_results:
                        break
        except Exception:
            continue
        if len(results) >= max_results:
            break

    if not results:
        return f"未找到包含 '{query}' 的内容"
    return f"找到 {len(results)} 处匹配:\n" + "\n".join(results)


# ============================================================
# 发送文件到飞书
# ============================================================

_feishu_channel = None


def set_feishu_channel_for_file(channel):
    """注入飞书通道实例"""
    global _feishu_channel
    _feishu_channel = channel


@registry.register(
    name="send_file_to_feishu",
    description="将文件发送到当前飞书会话。支持各种文件类型（PDF、Word、Excel、图片等）",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要发送的文件路径"},
        },
        "required": ["path"],
    },
    risk_level="low",
    category="file",
)
async def send_file_to_feishu(path: str) -> str:
    """发送文件到飞书"""
    if _feishu_channel is None:
        return "错误: 飞书通道未初始化，无法发送文件"
    
    chat_id = _feishu_channel.get_current_chat_id()
    if not chat_id:
        return "错误: 当前没有活跃的飞书会话"
    
    abs_path = _check_path(path)
    p = Path(abs_path)
    
    if not p.exists():
        return f"错误: 文件不存在: {path}"
    if not p.is_file():
        return f"错误: 不是文件: {path}"
    
    # 检查文件大小（飞书限制 30MB）
    size = p.stat().st_size
    if size > 30 * 1024 * 1024:
        return f"错误: 文件过大 ({size / 1024 / 1024:.1f}MB)，飞书限制 30MB"
    
    success = await _feishu_channel.send_file(chat_id, abs_path)
    
    if success:
        return f"✅ 文件已发送到飞书\n文件: {p.name}\n大小: {size:,} 字节"
    else:
        return "错误: 文件发送失败，请查看日志"
