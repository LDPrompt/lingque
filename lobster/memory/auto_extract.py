"""
🐦 灵雀 - P3 自动记忆提取

功能:
- Agent 自动识别并保存重要信息
- LLM 判断信息重要性
- 自动写入 MEMORY.md 和向量库
"""

import logging
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger("lingque.memory.extract")


@dataclass
class ExtractedMemory:
    """提取的记忆"""
    content: str
    category: str  # preference, fact, task, contact, etc.
    importance: float  # 0-1
    source: str  # user, assistant, tool


EXTRACTION_PROMPT = """分析以下对话，提取用户的重要信息（偏好、事实、联系人、任务习惯等）。

对话内容:
{conversation}

请以 JSON 数组格式返回提取的信息，每项包含:
- content: 信息内容（简洁明了）
- category: 类别 (preference/fact/contact/habit/important)
- importance: 重要性 0-1

只返回 JSON 数组，不要其他内容。如果没有值得记录的信息，返回空数组 []。

示例输出:
[
  {{"content": "用户偏好使用 Python 编程", "category": "preference", "importance": 0.8}},
  {{"content": "用户的老板邮箱是 boss@company.com", "category": "contact", "importance": 0.9}}
]"""


class MemoryExtractor:
    """
    自动记忆提取器

    在对话过程中自动识别重要信息，
    保存到 MEMORY.md 文件和向量记忆库。

    用法:
        extractor = MemoryExtractor(llm_router, memory_dir)
        memories = await extractor.extract(conversation_text)
        await extractor.save(memories)
    """

    def __init__(
        self,
        llm_router,
        memory_dir: str | Path = "./memory",
        auto_save_threshold: float = 0.6,
    ):
        self.llm_router = llm_router
        self.memory_dir = Path(memory_dir)
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.auto_save_threshold = auto_save_threshold

        self.memory_dir.mkdir(parents=True, exist_ok=True)

    async def extract(self, conversation: str) -> list[ExtractedMemory]:
        """
        从对话中提取重要信息

        Args:
            conversation: 对话文本

        Returns:
            提取的记忆列表
        """
        if not conversation or len(conversation) < 50:
            return []

        from ..agent.memory import redact_secrets
        safe_conversation = redact_secrets(conversation[:3000])
        prompt = EXTRACTION_PROMPT.format(conversation=safe_conversation)

        try:
            from ..llm.base import Message
            response = await self.llm_router.chat(
                messages=[Message(role="user", content=prompt)],
                temperature=0.3,
            )

            # 解析 JSON
            text = response.content.strip()
            # 提取 JSON 数组
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if not match:
                return []

            data = json.loads(match.group())
            memories = []
            for item in data:
                if isinstance(item, dict) and "content" in item:
                    memories.append(ExtractedMemory(
                        content=item.get("content", ""),
                        category=item.get("category", "fact"),
                        importance=float(item.get("importance", 0.5)),
                        source="extraction",
                    ))

            logger.info(f"从对话中提取了 {len(memories)} 条记忆")
            return memories

        except Exception as e:
            logger.error(f"记忆提取失败: {e}")
            return []

    async def save(self, memories: list[ExtractedMemory], to_vector: bool = True):
        """
        保存记忆

        Args:
            memories: 记忆列表
            to_vector: 是否同时保存到向量库
        """
        if not memories:
            return

        # 过滤低重要性记忆
        memories = [m for m in memories if m.importance >= self.auto_save_threshold]
        if not memories:
            return

        # 保存到 MEMORY.md
        await self._save_to_markdown(memories)

        # 保存到向量库
        if to_vector:
            await self._save_to_vector(memories)

    async def _save_to_markdown(self, memories: list[ExtractedMemory]):
        """保存到 Markdown 文件"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 读取现有内容
        existing = ""
        if self.memory_file.exists():
            existing = self.memory_file.read_text(encoding="utf-8")

        # 如果文件为空，添加头部
        if not existing.strip():
            existing = "# 🐦 灵雀记忆库\n\n> 自动提取的用户信息和偏好\n\n"

        # 按类别分组
        by_category = {}
        for m in memories:
            by_category.setdefault(m.category, []).append(m)

        # 生成新内容
        new_content = f"\n## {timestamp}\n\n"
        for category, items in by_category.items():
            category_name = {
                "preference": "📌 偏好",
                "fact": "📝 事实",
                "contact": "👤 联系人",
                "habit": "🔄 习惯",
                "important": "⭐ 重要",
            }.get(category, f"📋 {category}")

            new_content += f"### {category_name}\n\n"
            for m in items:
                new_content += f"- {m.content}\n"
            new_content += "\n"

        from ..agent.memory import redact_secrets
        new_content = redact_secrets(new_content)
        self.memory_file.write_text(existing + new_content, encoding="utf-8")
        logger.info(f"已保存 {len(memories)} 条记忆到 {self.memory_file}")

    async def _save_to_vector(self, memories: list[ExtractedMemory]):
        """保存到向量库"""
        try:
            from .vector_store import get_vector_memory
            vector_mem = get_vector_memory()
            if vector_mem is None:
                return

            items = [
                (m.content, {"category": m.category, "importance": m.importance})
                for m in memories
            ]
            vector_mem.add_batch(items)

        except ImportError:
            logger.warning("向量库依赖未安装，跳过向量存储")
        except Exception as e:
            logger.error(f"保存到向量库失败: {e}")

    def read_memory_file(self) -> str:
        """读取记忆文件内容（自动脱敏）"""
        if self.memory_file.exists():
            from ..agent.memory import redact_secrets
            return redact_secrets(self.memory_file.read_text(encoding="utf-8"))
        return ""

    def get_recent_memories(self, limit: int = 10) -> str:
        """获取最近的记忆摘要（用于上下文注入）"""
        content = self.read_memory_file()
        if not content:
            return ""

        # 简单取最后 N 行有内容的
        lines = [l for l in content.split("\n") if l.strip() and not l.startswith("#")]
        recent = lines[-limit:] if len(lines) > limit else lines
        return "\n".join(recent)
