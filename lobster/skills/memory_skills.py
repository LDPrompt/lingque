"""
🐦 灵雀 - P3 记忆增强相关技能

提供:
- 向量记忆搜索
- 添加记忆（向量库 + MEMORY.md）
- 查看/编辑长期记忆文件
- 查看记忆统计
"""

import logging
from pathlib import Path
from .registry import register, SkillResult

logger = logging.getLogger("lingque.skills.memory")

# 由 main.py 注入，指向 Memory 实例或 memory_dir 路径
_memory_dir: Path | None = None


def set_memory_dir(memory_dir: Path):
    """注入 memory_dir 路径，由 main.py 调用"""
    global _memory_dir
    _memory_dir = Path(memory_dir).resolve()
    logger.info(f"记忆技能目录: {_memory_dir}")


def _get_memory_file() -> Path:
    """获取 MEMORY.md 的绝对路径"""
    if _memory_dir:
        return _memory_dir / "MEMORY.md"
    return Path("./memory/MEMORY.md")


@register(
    name="memory_search",
    description="语义搜索记忆库，根据自然语言查找相关的历史信息和用户偏好",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询（自然语言）"},
            "top_k": {"type": "integer", "description": "返回结果数量（默认 5）"},
        },
        "required": ["query"],
    },
    risk_level="low",
)
async def memory_search(query: str, top_k: int = 5) -> SkillResult:
    """语义搜索记忆"""
    try:
        from ..memory.vector_store import get_vector_memory

        memory = get_vector_memory()
        if memory is None:
            return SkillResult(success=False, error="向量记忆库未启用或未初始化，请检查 VECTOR_MEMORY_ENABLED 配置")
        results = memory.search(query, top_k=top_k)

        if not results:
            return SkillResult(success=True, data="未找到相关记忆")

        output = [f"🔍 找到 {len(results)} 条相关记忆:\n"]
        for i, item in enumerate(results, 1):
            score_pct = int(item.score * 100)
            output.append(f"{i}. [{score_pct}%] {item.content}")
            if item.metadata:
                meta_str = ", ".join(f"{k}={v}" for k, v in item.metadata.items() if k != "timestamp")
                if meta_str:
                    output.append(f"   ({meta_str})")

        return SkillResult(success=True, data="\n".join(output))

    except ImportError:
        return SkillResult(
            success=False,
            error="向量记忆库未安装，请运行: pip install chromadb sentence-transformers"
        )
    except Exception as e:
        return SkillResult(success=False, error=str(e))


@register(
    name="memory_add",
    description="添加一条记忆到长期记忆文件 MEMORY.md（用户偏好、重要信息等）",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记住的内容"},
            "category": {"type": "string", "description": "类别: preference, fact, contact, important"},
        },
        "required": ["content"],
    },
    risk_level="low",
)
async def memory_add(content: str, category: str = "fact") -> SkillResult:
    """添加记忆到 MEMORY.md（同时尝试写入向量库）"""
    from datetime import datetime
    from ..agent.memory import redact_secrets
    content = redact_secrets(content)

    # 1. 写入 MEMORY.md（主存储，一定成功）
    memory_file = _get_memory_file()
    try:
        if memory_file.exists():
            current = memory_file.read_text(encoding="utf-8")
        else:
            current = "# 🐦 Long-Term Memory\n\n"

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        section_map = {
            "preference": "用户偏好",
            "fact": "重要信息",
            "contact": "联系人",
            "important": "重要信息",
        }
        section_name = section_map.get(category, "重要信息")

        # 追加到对应 section
        entry = f"\n- [{timestamp}] {content}"
        if f"## {section_name}" in current:
            current = current.replace(
                f"## {section_name}\n\n(尚无记录)",
                f"## {section_name}\n{entry}",
            )
            if "(尚无记录)" not in current.split(f"## {section_name}")[-1][:50]:
                # section 已有内容，追加
                parts = current.split(f"## {section_name}")
                before = parts[0] + f"## {section_name}"
                after = parts[1]
                next_section = after.find("\n## ")
                if next_section > 0:
                    current = before + after[:next_section].rstrip() + entry + "\n" + after[next_section:]
                else:
                    current = before + after.rstrip() + entry + "\n"
        else:
            current += f"\n## {section_name}\n{entry}\n"

        memory_file.write_text(current, encoding="utf-8")
        result_msg = f"✅ 已保存到 MEMORY.md\n内容: {content}\n类别: {section_name}"
    except Exception as e:
        logger.error(f"写入 MEMORY.md 失败: {e}")
        result_msg = f"⚠️ MEMORY.md 写入失败: {e}"

    # 2. 尝试写入向量库（可选，失败不影响主功能）
    try:
        from ..memory.vector_store import get_vector_memory
        memory = get_vector_memory()
        memory.add(content, metadata={"category": category})
    except Exception:
        pass

    return SkillResult(success=True, data=result_msg)


@register(
    name="memory_stats",
    description="查看记忆库统计信息",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk_level="low",
)
async def memory_stats() -> SkillResult:
    """记忆统计"""
    import os

    lines = ["📊 **记忆系统统计**\n"]

    # 1. MEMORY.md 信息
    memory_file = _get_memory_file()
    if memory_file.exists():
        size = memory_file.stat().st_size
        content = memory_file.read_text(encoding="utf-8")
        entry_count = content.count("\n- [")
        lines.append(f"**MEMORY.md**: {memory_file}")
        lines.append(f"  大小: {size:,} 字节, 记录数: {entry_count} 条")
    else:
        lines.append(f"**MEMORY.md**: 不存在 ({memory_file})")

    # 2. 日志信息
    if _memory_dir:
        log_dir = _memory_dir / "logs"
        if log_dir.exists():
            log_files = list(log_dir.glob("*.jsonl"))
            total_size = sum(f.stat().st_size for f in log_files)
            lines.append(f"**对话日志**: {len(log_files)} 个文件, 共 {total_size:,} 字节")

    # 3. 已安装技能
    if _memory_dir:
        manifest = _memory_dir / "transplanted_skills" / "manifest.json"
        if manifest.exists():
            import json
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                installed = len(data.get("installed", {}))
                skipped = len(data.get("skipped", {}))
                lines.append(f"**移植技能**: 已安装 {installed} 个, 跳过 {skipped} 个")
            except Exception:
                pass

    # 4. 向量库（可选）
    try:
        from ..memory.vector_store import get_vector_memory
        memory = get_vector_memory()
        stats = memory.get_stats()
        lines.append(f"**向量库**: {stats['count']} 条记忆, 路径: {stats['persist_dir']}")
    except Exception:
        lines.append("**向量库**: 未安装或未初始化")

    return SkillResult(success=True, data="\n".join(lines))


@register(
    name="memory_clear",
    description="清空所有向量记忆（谨慎使用！）",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk_level="high",
)
async def memory_clear() -> SkillResult:
    """清空记忆"""
    try:
        from ..memory.vector_store import get_vector_memory

        memory = get_vector_memory()
        if memory is None:
            return SkillResult(success=False, error="向量记忆库未启用或未初始化")
        count = memory.count()
        memory.clear()

        return SkillResult(
            success=True,
            data=f"✅ 已清空 {count} 条记忆"
        )

    except ImportError:
        return SkillResult(
            success=False,
            error="向量记忆库未安装"
        )
    except Exception as e:
        return SkillResult(success=False, error=str(e))


@register(
    name="read_memory_file",
    description="读取 MEMORY.md 文件内容（自动提取的用户信息和偏好）",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk_level="low",
)
async def read_memory_file() -> SkillResult:
    """读取记忆文件"""
    from ..agent.memory import redact_secrets
    memory_file = _get_memory_file()
    if not memory_file.exists():
        return SkillResult(success=True, data="MEMORY.md 文件不存在，还没有提取过记忆")

    content = memory_file.read_text(encoding="utf-8")
    if not content.strip():
        return SkillResult(success=True, data="MEMORY.md 文件为空")

    content = redact_secrets(content)
    return SkillResult(success=True, data=f"📍 路径: {memory_file}\n\n{content}")
