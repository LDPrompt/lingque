"""
🐦 灵雀 - P3 上下文压缩/截断

功能:
- 长对话自动总结
- 压缩后替换原消息
- 滑动窗口保持最近上下文
"""

import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger("lingque.memory.compressor")


SUMMARIZE_PROMPT = """请简洁地总结以下对话的关键信息，保留：
1. 用户的主要问题/请求
2. 重要的决策和结论
3. 关键的技术细节
4. 未完成的任务

对话内容:
{conversation}

请用 3-5 句话总结，不要遗漏重要信息。"""


@dataclass
class CompressedContext:
    """压缩后的上下文"""
    summary: str  # 压缩的历史摘要
    recent_messages: list  # 保留的最近消息
    original_count: int  # 原始消息数
    compressed_count: int  # 压缩后数量


class ContextCompressor:
    """
    上下文压缩器

    当对话历史过长时，自动总结早期对话，
    保持滑动窗口内的最近消息完整。

    用法:
        compressor = ContextCompressor(llm_router)
        compressed = await compressor.compress(messages, max_tokens=8000)
    """

    def __init__(
        self,
        llm_router,
        window_size: int = 10,  # 保留最近 N 条消息不压缩
        compress_threshold: int = 20,  # 超过 N 条消息时触发压缩
        max_summary_length: int = 500,  # 摘要最大长度
    ):
        self.llm_router = llm_router
        self.window_size = window_size
        self.compress_threshold = compress_threshold
        self.max_summary_length = max_summary_length

    async def compress(
        self,
        messages: list,
        max_tokens: Optional[int] = None,
    ) -> CompressedContext:
        """
        压缩消息历史

        Args:
            messages: 消息列表 (Message 对象或 dict)
            max_tokens: 最大 token 数限制

        Returns:
            压缩后的上下文
        """
        if len(messages) <= self.compress_threshold:
            # 不需要压缩
            return CompressedContext(
                summary="",
                recent_messages=messages,
                original_count=len(messages),
                compressed_count=len(messages),
            )

        # 分割：早期消息 vs 保留的最近消息
        to_compress = messages[:-self.window_size]
        to_keep = messages[-self.window_size:]

        # 生成摘要
        summary = await self._summarize(to_compress)

        logger.info(
            f"压缩上下文: {len(to_compress)} 条消息 → 摘要 ({len(summary)} 字)"
        )

        return CompressedContext(
            summary=summary,
            recent_messages=to_keep,
            original_count=len(messages),
            compressed_count=len(to_keep) + 1,  # +1 for summary
        )

    async def _summarize(self, messages: list) -> str:
        """总结消息"""
        # 将消息转换为文本
        conversation = self._messages_to_text(messages)

        prompt = SUMMARIZE_PROMPT.format(conversation=conversation[:4000])

        try:
            from ..llm.base import Message
            response = await self.llm_router.chat(
                messages=[Message(role="user", content=prompt)],
                temperature=0.3,
            )

            summary = (response.content or "").strip()
            # 限制长度
            if len(summary) > self.max_summary_length:
                summary = summary[:self.max_summary_length] + "..."

            return summary

        except Exception as e:
            logger.error(f"生成摘要失败: {e}")
            # 降级：简单截取
            return f"[早期对话摘要，共 {len(messages)} 条消息]"

    def _messages_to_text(self, messages: list) -> str:
        """将消息列表转换为文本"""
        lines = []
        for msg in messages:
            if hasattr(msg, "role"):
                role = msg.role
                content = msg.content or ""
            else:
                role = msg.get("role", "unknown")
                content = msg.get("content") or ""

            if role == "user":
                lines.append(f"用户: {content}")
            elif role == "assistant":
                lines.append(f"助手: {content}")
            elif role == "tool":
                preview = content[:200] + ("..." if len(content) > 200 else "")
                lines.append(f"[工具结果]: {preview}")
            elif role == "system":
                pass  # 跳过 system 消息

        return "\n".join(lines)

    def build_compressed_messages(
        self,
        compressed: CompressedContext,
        system_prompt: str = "",
    ) -> list:
        """
        构建压缩后的消息列表

        Args:
            compressed: 压缩结果
            system_prompt: 系统提示词

        Returns:
            可以直接发送给 LLM 的消息列表
        """
        from ..llm.base import Message

        messages = []

        # 系统提示
        if system_prompt:
            messages.append(Message(role="system", content=system_prompt))

        # 历史摘要（如果有）
        if compressed.summary:
            summary_msg = f"[以下是早期对话的摘要]\n\n{compressed.summary}\n\n[以下是最近的对话]"
            messages.append(Message(role="system", content=summary_msg))

        # 最近的消息
        messages.extend(compressed.recent_messages)

        return messages


class SlidingWindowManager:
    """
    滑动窗口管理器

    更细粒度地控制上下文窗口
    """

    def __init__(
        self,
        max_messages: int = 50,
        max_tokens: int = 16000,
        compressor: Optional[ContextCompressor] = None,
    ):
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self.compressor = compressor
        self._cached_summary = ""

    async def process(self, messages: list) -> list:
        """
        处理消息列表，确保不超过限制

        Args:
            messages: 原始消息列表

        Returns:
            处理后的消息列表
        """
        # 简单截断
        if len(messages) > self.max_messages:
            if self.compressor:
                compressed = await self.compressor.compress(messages)
                self._cached_summary = compressed.summary
                return self.compressor.build_compressed_messages(compressed)
            else:
                # 无压缩器，简单截断
                logger.warning(f"消息过多 ({len(messages)})，截断到 {self.max_messages}")
                return messages[-self.max_messages:]

        return messages

    def estimate_tokens(self, messages: list) -> int:
        """粗略估算 token 数"""
        total = 0
        for msg in messages:
            content = msg.content if hasattr(msg, "content") else msg.get("content")
            content = content or ""  # 处理 None
            # 粗略估算：中文约 2 token/字，英文约 0.75 token/字
            total += len(content) * 1.5
        return int(total)
